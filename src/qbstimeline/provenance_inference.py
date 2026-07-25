from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


_NS_PER_SECOND = 1_000_000_000
_MATCH_TOLERANCE_NS = 1


@dataclass(frozen=True)
class _Q1asmLine:
    line_number: int
    raw: str
    instruction: str
    operands: tuple[str, ...]
    start_ns: int
    duration_ns: int | None


@dataclass(frozen=True)
class _Candidate:
    sequencer_id: str
    instruction: str
    event_line: _Q1asmLine
    context_lines: tuple[_Q1asmLine, ...]
    start_ns: int
    start_line: int
    end_line: int
    instruction_roles: list[str]
    exact_duration_operand: bool


def infer_q1asm_provenance(
    symbolic_blocks: list[dict[str, Any]],
    q1asm_programs: Sequence[Any],
    *,
    reserved_q1asm_ranges: Sequence[dict[str, Any]] | None = None,
    context_q1asm_provenance: Sequence[dict[str, Any]] | None = None,
    context_symbolic_blocks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    parsed_programs = [
        (_program_sequencer_id(program), _parse_q1asm_lines(_program_text(program)))
        for program in q1asm_programs
        if _program_sequencer_id(program) and _program_text(program)
    ]
    reserved_ranges = _normalize_reserved_ranges(reserved_q1asm_ranges or [])
    rows: list[dict[str, Any]] = []
    unmatched_blocks: list[dict[str, Any]] = []

    for block in symbolic_blocks:
        instruction = _instruction_for_block(block)
        target_start_ns = _seconds_to_ns(block.get("abs_time"))
        target_duration_ns = _seconds_to_ns(block.get("duration"))
        if instruction is None or target_start_ns is None or target_duration_ns is None:
            continue

        candidates: list[_Candidate] = []
        for sequencer_id, lines in parsed_programs:
            candidates.extend(
                _candidates_for_block(
                    sequencer_id=sequencer_id,
                    lines=lines,
                    instruction=instruction,
                    block=block,
                    target_start_ns=target_start_ns,
                    target_duration_ns=target_duration_ns,
                    reserved_ranges=reserved_ranges,
                )
            )
        if len(candidates) != 1:
            unmatched_blocks.append(block)
            continue

        candidate = candidates[0]
        row = _record_for_candidate(block, candidate, target_duration_ns)
        rows.append(row)
        _reserve_row_range(reserved_ranges, row)

    matched_source_ids = {row["source_id"] for row in rows}
    paired_rows = _infer_pulses_from_acquisition_context(
        [block for block in unmatched_blocks if str(block.get("id")) not in matched_source_ids],
        context_symbolic_blocks or symbolic_blocks,
        [*(context_q1asm_provenance or []), *rows],
        parsed_programs,
        reserved_ranges,
    )
    rows.extend(paired_rows)
    _reserve_row_ranges(reserved_ranges, paired_rows)
    matched_source_ids.update(row["source_id"] for row in paired_rows)
    range_rows = _infer_lowering_range_provenance(
        [block for block in unmatched_blocks if str(block.get("id")) not in matched_source_ids],
        parsed_programs,
        reserved_ranges,
    )
    rows.extend(range_rows)
    source_order = {
        str(block.get("id")): index
        for index, block in enumerate(symbolic_blocks)
    }
    rows.sort(key=lambda row: source_order.get(str(row.get("source_id")), len(source_order)))
    return rows


def _candidates_for_block(
    *,
    sequencer_id: str,
    lines: list[_Q1asmLine],
    instruction: str,
    block: dict[str, Any],
    target_start_ns: int,
    target_duration_ns: int,
    reserved_ranges: set[tuple[str, int, int]],
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for index, line in enumerate(lines):
        if not _instruction_matches_block_instruction(line.instruction, instruction) or line.duration_ns is None:
            continue
        if not _line_matches_block_operands(line, block):
            continue
        if abs(line.start_ns - target_start_ns) > _MATCH_TOLERANCE_NS:
            continue

        end_index, range_duration_ns = _range_covering_duration(lines, index, target_duration_ns)
        exact_duration_operand = line.duration_ns == target_duration_ns
        if not exact_duration_operand and range_duration_ns != target_duration_ns:
            continue

        start_index = _include_setup_lines(lines, index)
        if _range_is_reserved(reserved_ranges, sequencer_id, lines[start_index].line_number, lines[end_index].line_number):
            continue
        roles = [
            role_line.instruction
            for role_line in lines[start_index : end_index + 1]
            if role_line.instruction
        ]
        candidates.append(
            _Candidate(
                sequencer_id=sequencer_id,
                instruction=line.instruction,
                event_line=line,
                context_lines=tuple(lines[start_index : end_index + 1]),
                start_ns=line.start_ns,
                start_line=lines[start_index].line_number,
                end_line=lines[end_index].line_number,
                instruction_roles=roles,
                exact_duration_operand=exact_duration_operand,
            )
        )
    return candidates


def _infer_pulses_from_acquisition_context(
    unmatched_blocks: list[dict[str, Any]],
    all_blocks: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
    parsed_programs: list[tuple[str, list[_Q1asmLine]]],
    reserved_ranges: set[tuple[str, int, int]],
) -> list[dict[str, Any]]:
    rows_by_source_id = {str(row.get("source_id")): row for row in existing_rows}
    acquisition_blocks = [block for block in all_blocks if _instruction_for_block(block) == "acquire"]
    rows: list[dict[str, Any]] = []
    for block in unmatched_blocks:
        if _instruction_for_block(block) != "play":
            continue
        target_start_ns = _seconds_to_ns(block.get("abs_time"))
        target_duration_ns = _seconds_to_ns(block.get("duration"))
        if target_start_ns is None or target_duration_ns is None:
            continue
        paired_sequencers = {
            str(row.get("sequencer_id") or "")
            for acquisition in acquisition_blocks
            if _blocks_share_measurement_context(block, acquisition)
            for row in [rows_by_source_id.get(str(acquisition.get("id")))]
            if row is not None and row.get("sequencer_id")
        }
        if len(paired_sequencers) != 1:
            continue
        sequencer_id = next(iter(paired_sequencers))
        lines = next((candidate_lines for candidate_id, candidate_lines in parsed_programs if candidate_id == sequencer_id), None)
        if lines is None:
            continue
        candidates = _candidates_for_block(
            sequencer_id=sequencer_id,
            lines=lines,
            instruction="play",
            block=block,
            target_start_ns=target_start_ns,
            target_duration_ns=target_duration_ns,
            reserved_ranges=reserved_ranges,
        )
        if len(candidates) == 1:
            row = _record_for_candidate(block, candidates[0], target_duration_ns)
            rows.append(row)
            _reserve_row_range(reserved_ranges, row)
    return rows


def _blocks_share_measurement_context(block: dict[str, Any], acquisition: dict[str, Any]) -> bool:
    for key in ("schedulable_id", "operation_id"):
        block_value = block.get(key)
        acquisition_value = acquisition.get(key)
        if block_value and acquisition_value and str(block_value) == str(acquisition_value):
            return True
    return False


def _normalize_reserved_ranges(rows: Sequence[dict[str, Any]]) -> set[tuple[str, int, int]]:
    ranges: set[tuple[str, int, int]] = set()
    for row in rows:
        _reserve_row_range(ranges, row)
    return ranges


def _reserve_row_ranges(
    reserved_ranges: set[tuple[str, int, int]],
    rows: Sequence[dict[str, Any]],
) -> None:
    for row in rows:
        _reserve_row_range(reserved_ranges, row)


def _reserve_row_range(
    reserved_ranges: set[tuple[str, int, int]],
    row: dict[str, Any],
) -> None:
    sequencer_id = str(row.get("sequencer_id") or row.get("sequencer") or "")
    start_line = row.get("q1asm_line_start")
    end_line = row.get("q1asm_line_end")
    if sequencer_id and isinstance(start_line, int) and isinstance(end_line, int) and start_line <= end_line:
        reserved_ranges.add((sequencer_id, start_line, end_line))


def _range_is_reserved(
    reserved_ranges: set[tuple[str, int, int]],
    sequencer_id: str,
    start_line: int,
    end_line: int,
) -> bool:
    return any(
        reserved_sequencer == sequencer_id
        and start_line <= reserved_end
        and end_line >= reserved_start
        for reserved_sequencer, reserved_start, reserved_end in reserved_ranges
    )


def _infer_lowering_range_provenance(
    blocks: list[dict[str, Any]],
    parsed_programs: list[tuple[str, list[_Q1asmLine]]],
    reserved_ranges: set[tuple[str, int, int]],
) -> list[dict[str, Any]]:
    ordered_blocks = [
        block
        for block in blocks
        if _instruction_for_block(block) == "play"
        and _seconds_to_ns(block.get("abs_time")) is not None
        and _seconds_to_ns(block.get("duration")) is not None
    ]
    if not ordered_blocks:
        return []
    ordered_blocks.sort(key=lambda block: (_seconds_to_ns(block.get("abs_time")) or 0, str(block.get("id"))))
    if len(ordered_blocks) == 1:
        return _infer_independent_lowering_ranges(ordered_blocks, parsed_programs, reserved_ranges)

    sequencer_matches: list[tuple[str, list[_Candidate]]] = []
    for sequencer_id, lines in parsed_programs:
        sequences = _matching_lowering_sequences(ordered_blocks, sequencer_id, lines, reserved_ranges)
        if sequences:
            sequencer_matches.append((sequencer_id, sequences[0]))
    if len(sequencer_matches) != 1:
        return _infer_independent_lowering_ranges(ordered_blocks, parsed_programs, reserved_ranges)

    _, sequence = sequencer_matches[0]
    rows: list[dict[str, Any]] = []
    for block, candidate in zip(ordered_blocks, sequence, strict=True):
        target_duration_ns = _seconds_to_ns(block.get("duration"))
        if target_duration_ns is None:
            continue
        rows.append(_record_for_candidate(block, candidate, target_duration_ns))
    return rows


def _infer_independent_lowering_ranges(
    blocks: list[dict[str, Any]],
    parsed_programs: list[tuple[str, list[_Q1asmLine]]],
    reserved_ranges: set[tuple[str, int, int]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for block in blocks:
        target_duration_ns = _seconds_to_ns(block.get("duration"))
        if target_duration_ns is None:
            continue
        candidates: list[_Candidate] = []
        for sequencer_id, lines in parsed_programs:
            candidates.extend(
                _lowering_range_candidates_for_block(
                    sequencer_id=sequencer_id,
                    lines=lines,
                    block=block,
                    target_start_ns=_seconds_to_ns(block.get("abs_time")),
                    target_duration_ns=target_duration_ns,
                    reserved_ranges=reserved_ranges,
                )
        )
        if len(candidates) == 1:
            row = _record_for_candidate(block, candidates[0], target_duration_ns)
            rows.append(row)
            _reserve_row_range(reserved_ranges, row)
    return rows


def _matching_lowering_sequences(
    blocks: list[dict[str, Any]],
    sequencer_id: str,
    lines: list[_Q1asmLine],
    reserved_ranges: set[tuple[str, int, int]],
) -> list[list[_Candidate]]:
    candidates_by_block = [
        _lowering_range_candidates_for_block(
            sequencer_id=sequencer_id,
            lines=lines,
            block=block,
            target_start_ns=_seconds_to_ns(block.get("abs_time")),
            target_duration_ns=_seconds_to_ns(block.get("duration")) or 0,
            reserved_ranges=reserved_ranges,
        )
        for block in blocks
    ]
    if any(not candidates for candidates in candidates_by_block):
        return []

    sequences: list[list[_Candidate]] = []
    for first_candidate in candidates_by_block[0]:
        sequence = [first_candidate]
        previous_end_line = first_candidate.end_line
        for block, candidates in zip(blocks[1:], candidates_by_block[1:], strict=True):
            block_start_ns = _seconds_to_ns(block.get("abs_time")) or 0
            match = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.start_line > previous_end_line
                    and abs(candidate.start_ns - block_start_ns) <= _MATCH_TOLERANCE_NS
                ),
                None,
            )
            if match is None:
                break
            sequence.append(match)
            previous_end_line = match.end_line
        if len(sequence) == len(blocks):
            sequences.append(sequence)
    return sequences


def _lowering_range_candidates_for_block(
    *,
    sequencer_id: str,
    lines: list[_Q1asmLine],
    block: dict[str, Any],
    target_start_ns: int | None,
    target_duration_ns: int,
    reserved_ranges: set[tuple[str, int, int]],
) -> list[_Candidate]:
    if target_duration_ns <= 0:
        return []
    candidates: list[_Candidate] = []
    for start_index, line in enumerate(lines):
        if line.duration_ns is None or not _is_pulse_lowering_timing_line(line):
            continue
        if target_start_ns is not None and abs(line.start_ns - target_start_ns) > _MATCH_TOLERANCE_NS:
            continue
        total_duration_ns = 0
        end_index = start_index
        for next_index in range(start_index, len(lines)):
            next_line = lines[next_index]
            if next_line.duration_ns is not None:
                if not _is_pulse_lowering_timing_line(next_line):
                    break
                total_duration_ns += next_line.duration_ns
            end_index = next_index
            if total_duration_ns >= target_duration_ns:
                break
        if total_duration_ns != target_duration_ns:
            continue
        range_lines = lines[start_index : end_index + 1]
        if not _range_matches_pulse_block(range_lines, block):
            continue
        display_start_index = _include_setup_lines(lines, start_index)
        if _range_is_reserved(
            reserved_ranges,
            sequencer_id,
            lines[display_start_index].line_number,
            lines[end_index].line_number,
        ):
            continue
        display_lines = lines[display_start_index : end_index + 1]
        event_line = _event_line_for_range(range_lines) or line
        exact_duration_operand = event_line.duration_ns == target_duration_ns
        candidates.append(
            _Candidate(
                sequencer_id=sequencer_id,
                instruction=event_line.instruction,
                event_line=event_line,
                context_lines=tuple(display_lines),
                start_ns=line.start_ns,
                start_line=lines[display_start_index].line_number,
                end_line=lines[end_index].line_number,
                instruction_roles=[
                    role_line.instruction
                    for role_line in display_lines
                    if role_line.instruction and not role_line.instruction.endswith(":")
                ],
                exact_duration_operand=exact_duration_operand,
            )
        )
    return candidates


def _is_pulse_lowering_timing_line(line: _Q1asmLine) -> bool:
    return line.instruction in {"play", "wait", "upd_param"}


def _range_matches_pulse_block(lines: list[_Q1asmLine], block: dict[str, Any]) -> bool:
    if any(line.instruction == "play" for line in lines):
        return _range_mentions_block_kind(lines, block) or not _block_kind_terms(block)
    return _range_mentions_block_kind(lines, block)


def _range_mentions_block_kind(lines: list[_Q1asmLine], block: dict[str, Any]) -> bool:
    terms = _block_kind_terms(block)
    if not terms:
        return False
    text = " ".join(line.raw.lower() for line in lines)
    return any(term in text for term in terms)


def _block_kind_terms(block: dict[str, Any]) -> set[str]:
    terms: set[str] = set()
    for value in (block.get("kind"), block.get("label")):
        if isinstance(value, str) and value:
            lowered = value.lower()
            terms.add(lowered)
            if not lowered.endswith("pulse"):
                terms.add(f"{lowered}pulse")
    return terms


def _event_line_for_range(lines: list[_Q1asmLine]) -> _Q1asmLine | None:
    for line in lines:
        if line.instruction == "play":
            return line
    for line in lines:
        if line.duration_ns is not None:
            return line
    return None


def _range_covering_duration(
    lines: list[_Q1asmLine],
    event_index: int,
    target_duration_ns: int,
) -> tuple[int, int]:
    event = lines[event_index]
    total_duration_ns = event.duration_ns or 0
    end_index = event_index
    if total_duration_ns >= target_duration_ns:
        return end_index, total_duration_ns

    for next_index in range(event_index + 1, len(lines)):
        line = lines[next_index]
        if line.instruction != "wait" or line.duration_ns is None:
            break
        total_duration_ns += line.duration_ns
        end_index = next_index
        if total_duration_ns >= target_duration_ns:
            break
    return end_index, total_duration_ns


def _include_setup_lines(lines: list[_Q1asmLine], event_index: int) -> int:
    start_index = event_index
    event = lines[event_index]
    while start_index > 0:
        previous = lines[start_index - 1]
        if previous.start_ns == event.start_ns and previous.instruction.startswith("set_"):
            start_index -= 1
            continue
        break
    return start_index


def _record_for_candidate(
    block: dict[str, Any],
    candidate: _Candidate,
    target_duration_ns: int,
) -> dict[str, Any]:
    operand_mappings: list[dict[str, Any]] = _symbolic_parameter_operand_mappings(block, candidate)
    duration_value_id = block.get("duration_value_id")
    if candidate.exact_duration_operand and isinstance(duration_value_id, str):
        duration_instruction = candidate.event_line.instruction
        operand_mappings.append(
            {
                "line": candidate.event_line.line_number,
                "instruction": duration_instruction,
                "operand_index": _duration_operand_index(duration_instruction),
                "role": "duration",
                "numeric_value": target_duration_ns,
                "unit": "ns",
                "source_value_id": duration_value_id,
            }
        )
    elif isinstance(duration_value_id, str):
        operand_mappings.append(
            {
                "line": candidate.event_line.line_number,
                "line_end": candidate.end_line,
                "instruction": candidate.event_line.instruction,
                "operand_index": _duration_operand_index(candidate.event_line.instruction),
                "role": "duration_range",
                "numeric_value": target_duration_ns,
                "unit": "ns",
                "source_value_id": duration_value_id,
            }
        )

    source_kind = str(block.get("role") or "unknown")
    return {
        "source_id": str(block["id"]),
        "source_kind": source_kind,
        "operation_id": str(block.get("operation_id", "")),
        "schedulable_id": str(block.get("schedulable_id", "")),
        "sequencer_id": candidate.sequencer_id,
        "q1asm_line_start": candidate.start_line,
        "q1asm_line_end": candidate.end_line,
        "instruction_roles": candidate.instruction_roles,
        "operand_mappings": operand_mappings,
        "confidence": "inferred",
        "inference_reason": (
            f"unique {candidate.instruction} lowering range matched {source_kind} time and duration"
            if candidate.instruction == "play" and not candidate.exact_duration_operand
            else f"unique {candidate.instruction} event matched {source_kind} time and duration"
        ),
    }


def _duration_operand_index(instruction: str) -> int:
    return 2 if instruction == "play" or _is_acquisition_instruction(instruction) else 0


def _symbolic_parameter_operand_mappings(block: dict[str, Any], candidate: _Candidate) -> list[dict[str, Any]]:
    parameter_value_ids = block.get("parameter_value_ids")
    if not isinstance(parameter_value_ids, dict):
        return []
    mappings: list[dict[str, Any]] = []
    amp_value_id = parameter_value_ids.get("amp")
    amp_value = _parameter_value(block, "amp")
    if isinstance(amp_value_id, str) and isinstance(amp_value, int | float):
        setup_line = _setup_line_for_instruction(candidate, "set_awg_gain")
        if setup_line is not None:
            mappings.append(
                {
                    "line": setup_line.line_number,
                    "instruction": setup_line.instruction,
                    "operand_index": 0,
                    "role": "amplitude",
                    "numeric_value": amp_value,
                    "unit": None,
                    "source_value_id": amp_value_id,
                }
            )
    offset_line = _setup_line_for_instruction(candidate, "set_awg_offs")
    if offset_line is not None:
        for key, operand_index in (("offset_path_0", 0), ("offset_path_1", 1)):
            offset_value_id = parameter_value_ids.get(key)
            offset_value = _parameter_value(block, key)
            if isinstance(offset_value_id, str) and isinstance(offset_value, int | float):
                mappings.append(
                    {
                        "line": offset_line.line_number,
                        "instruction": offset_line.instruction,
                        "operand_index": operand_index,
                        "role": "offset",
                        "numeric_value": offset_value,
                        "unit": None,
                        "source_value_id": offset_value_id,
                    }
                )
    return mappings


def _parameter_value(block: dict[str, Any], key: str) -> Any:
    parameters = block.get("parameters")
    if not isinstance(parameters, dict):
        return None
    return parameters.get(key)


def _setup_line_for_instruction(candidate: _Candidate, instruction: str) -> _Q1asmLine | None:
    for line in candidate.context_lines:
        if line.line_number >= candidate.event_line.line_number:
            break
        if line.instruction == instruction:
            return line
    return None


def _parse_q1asm_lines(program: str) -> list[_Q1asmLine]:
    rows: list[_Q1asmLine] = []
    time_ns = 0
    for index, raw_line in enumerate(program.splitlines(), start=1):
        instruction, operands = _parse_instruction(raw_line)
        duration_ns = _duration_ns(instruction, operands)
        rows.append(
            _Q1asmLine(
                line_number=index,
                raw=raw_line,
                instruction=instruction,
                operands=tuple(operands),
                start_ns=time_ns,
                duration_ns=duration_ns,
            )
        )
        if _advances_time(instruction) and duration_ns is not None:
            time_ns += duration_ns
    return rows


def _parse_instruction(raw_line: str) -> tuple[str, list[str]]:
    line = raw_line.split("#", 1)[0].strip()
    if not line:
        return "", []
    parts = line.split(maxsplit=1)
    instruction = parts[0].lower()
    if len(parts) == 1:
        return instruction, []
    operands = [operand for operand in re.split(r"[,\s]+", parts[1].strip()) if operand]
    return instruction, operands


def _duration_ns(instruction: str, operands: list[str]) -> int | None:
    if instruction in {"wait", "upd_param", "wait_sync"}:
        return _int_operand(operands, 0)
    if instruction == "play" or _is_acquisition_instruction(instruction):
        return _int_operand(operands, 2)
    return None


def _int_operand(operands: list[str], index: int) -> int | None:
    if index >= len(operands):
        return None
    try:
        return int(float(operands[index]))
    except ValueError:
        return None


def _line_matches_block_operands(line: _Q1asmLine, block: dict[str, Any]) -> bool:
    if not _is_acquisition_instruction(line.instruction):
        return True
    acq_channel = _acq_channel(block)
    if acq_channel is None:
        return True
    return _int_operand(list(line.operands), 0) == acq_channel


def _acq_channel(block: dict[str, Any]) -> int | None:
    parameters = block.get("parameters")
    if not isinstance(parameters, dict):
        return None
    value = parameters.get("acq_channel")
    if not isinstance(value, int | float):
        return None
    return int(value)


def _instruction_for_block(block: dict[str, Any]) -> str | None:
    role = block.get("role")
    if role == "pulse":
        return "play"
    if role == "acquisition":
        return "acquire"
    return None


def _instruction_matches_block_instruction(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    return expected == "acquire" and _is_acquisition_instruction(actual)


def _is_acquisition_instruction(instruction: str) -> bool:
    return instruction == "acquire" or instruction.startswith("acquire_")


def _advances_time(instruction: str) -> bool:
    return instruction in {"wait", "upd_param", "play"} or _is_acquisition_instruction(instruction)


def _seconds_to_ns(value: Any) -> int | None:
    if not isinstance(value, int | float):
        return None
    return round(float(value) * _NS_PER_SECOND)


def _program_sequencer_id(program: Any) -> str:
    if isinstance(program, dict):
        return str(program.get("sequencer_id") or program.get("sequencer") or "")
    return str(getattr(program, "sequencer_id", "") or getattr(program, "sequencer", ""))


def _program_text(program: Any) -> str:
    if isinstance(program, dict):
        return str(program.get("program") or program.get("text") or "")
    return str(getattr(program, "program", "") or getattr(program, "text", ""))
