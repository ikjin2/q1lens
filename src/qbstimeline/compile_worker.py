from __future__ import annotations

import ast
import importlib.util
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from qbstimeline._access import get_value as _get_value
from qbstimeline._access import unwrap as _unwrap_user_dict
from qbstimeline.artifacts import generate_native_artifacts
from qbstimeline.extract.symbolic_pulses import extract_symbolic_pulse_layer
from qbstimeline.ir.serialize import make_qbs_ir
from qbstimeline.ir.validation import validate_qbs_ir
from qbstimeline.notebook import (
    GeneratedLineMapper,
    execute_selected_notebook_cells,
    load_notebook_code_cells,
)
from qbstimeline.provenance import normalize_q1asm_provenance
from qbstimeline.provenance_inference import infer_q1asm_provenance
from qbstimeline.project import ProjectConfig
from qbstimeline.source_tracing import SourceTrace, SourceTraceLocation, traced_schedule_adds


@dataclass(frozen=True)
class Q1ASMProgram:
    sequencer_id: str
    relative_file: Path
    program: str
    path: tuple[str, ...]


@dataclass(frozen=True)
class AnalysisResult:
    ir: dict[str, Any]
    q1asm_programs: list[Q1ASMProgram]


@dataclass(frozen=True)
class ScheduleStructure:
    operations: list[dict[str, Any]]
    control_flow_blocks: list[dict[str, Any]]


MANUAL_SWEEP_COMPACT_MIN = 2
SCHEDULE_REPETITION_ID = "__schedule_repetition"
GENERATED_QBLOX_VARIABLE_RE = re.compile(r"^Var[0-9a-fA-F]{32}$")


def analyze_project(config: ProjectConfig) -> AnalysisResult:
    source_trace = SourceTrace()
    if config.notebook_schedule is not None:
        notebook_config = config.notebook_schedule
        with traced_schedule_adds() as source_trace:
            namespace = execute_selected_notebook_cells(
                notebook_config.notebook,
                setup_tags=notebook_config.setup_tags,
                schedule_tag=notebook_config.schedule_tag,
            )
            schedule = namespace[notebook_config.schedule_variable]
        compiler = _compiler_from_value(namespace[notebook_config.compiler_variable])
    else:
        if config.schedule_file is None:
            raise RuntimeError("Project config does not define a schedule file")
        module = _load_python_module(config.schedule_file)
        schedule_factory = _entrypoint(module, config.schedule_entrypoint)
        compiler_factory = _entrypoint(module, config.compiler_entrypoint)

        with traced_schedule_adds() as source_trace:
            schedule = schedule_factory()
        compiler = compiler_factory()
    _merge_object_source_trace(source_trace, schedule)
    source_structure = _extract_schedule_structure(schedule, source_trace=source_trace)
    compile_warning: str | None = None
    try:
        compiled_schedule = compiler.compile(schedule)
    except Exception as exc:
        if not source_structure.operations:
            raise
        compiled_schedule = schedule
        compile_warning = f"compile failed; rendered compact source preview only: {exc}"
    if source_structure.control_flow_blocks:
        structure = source_structure
        symbolic_layer = extract_symbolic_pulse_layer(schedule)
        timing_table: list[dict[str, Any]] = []
    else:
        _merge_object_source_trace(source_trace, compiled_schedule)
        structure = _extract_schedule_structure(compiled_schedule, source_trace=source_trace)
        symbolic_layer = extract_symbolic_pulse_layer(compiled_schedule)
        timing_table = _extract_timing_table(compiled_schedule)
    symbolic_values = symbolic_layer["symbolic_values"]
    symbolic_pulses = symbolic_layer["symbolic_pulses"]
    warnings: list[str] = []
    if compile_warning:
        warnings.append(compile_warning)
    artifacts, artifact_warnings = generate_native_artifacts(
        schedule=schedule,
        compiled_schedule=compiled_schedule,
        output_dir=config.output_dir,
        config=config,
    )
    warnings.extend(artifact_warnings)

    compiled_instructions = _get_value(compiled_schedule, "compiled_instructions", {})
    q1asm_programs = extract_q1asm_programs(compiled_instructions)
    q1asm_provenance = _valid_q1asm_provenance_rows(
        normalize_q1asm_provenance(compiled_schedule),
        q1asm_programs,
    )
    q1asm_provenance = _merge_same_source_operand_inference(
        q1asm_provenance,
        symbolic_pulses,
        q1asm_programs,
    )
    inferred_q1asm_provenance = infer_q1asm_provenance(
        [
            block
            for block in symbolic_pulses
            if str(block.get("id")) not in {str(row.get("source_id")) for row in q1asm_provenance}
        ],
        q1asm_programs,
        reserved_q1asm_ranges=q1asm_provenance,
        context_q1asm_provenance=q1asm_provenance,
        context_symbolic_blocks=symbolic_pulses,
    )
    q1asm_provenance = [*q1asm_provenance, *inferred_q1asm_provenance]
    _write_q1asm_files(config.output_dir, q1asm_programs)
    if config.low_level_q1timeline and q1asm_programs:
        from qbstimeline.adapters.q1timeline import write_q1timeline_project

        write_q1timeline_project(config.output_dir, q1asm_programs)
    else:
        (config.output_dir / "q1timeline.yml").unlink(missing_ok=True)

    operations = structure.operations
    ir = make_qbs_ir(
        project_root=config.root,
        schedule_name=_schedule_name(compiled_schedule, schedule),
        operations=operations,
        control_flow_blocks=structure.control_flow_blocks,
        timing_table=timing_table,
        q1asm_programs=q1asm_programs,
        low_level_q1timeline=config.low_level_q1timeline,
        symbolic_values=symbolic_values,
        symbolic_pulses=symbolic_pulses,
        q1asm_provenance=q1asm_provenance,
        source_map=_build_schedule_source_map(config=config, structure=structure),
        capabilities={
            "operations": bool(operations),
            "symbolic_pulses": bool(symbolic_pulses),
            "q1asm": bool(q1asm_programs),
            "artifacts": any(row.get("status") == "ok" for row in artifacts.values()),
        },
        warnings=warnings,
        artifacts=artifacts,
    )
    validation_diagnostics = validate_qbs_ir(ir)
    ir["ir_diagnostics"] = [diagnostic.to_ir() for diagnostic in validation_diagnostics]
    validation_warnings = [
        f"IR invariant {diagnostic.to_warning()}"
        for diagnostic in validation_diagnostics
    ]
    if validation_warnings:
        ir["warnings"] = [*ir.get("warnings", []), *validation_warnings]
    return AnalysisResult(ir=ir, q1asm_programs=q1asm_programs)


def _extract_schedule_source_map(schedule_file: Path | None, project_root: Path) -> dict[str, Any]:
    if schedule_file is None:
        return {"schedulables": {}}
    try:
        source = schedule_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(schedule_file))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return {"schedulables": {}}

    schedulables: dict[str, dict[str, Any]] = {}
    try:
        file_name = schedule_file.relative_to(project_root).as_posix()
    except ValueError:
        file_name = schedule_file.name

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "add":
            continue
        label = _literal_keyword_string(node, "label")
        if not label:
            continue
        schedulables[label] = {
            "file": file_name,
            "line": node.lineno,
            "column": node.col_offset,
            "label": label,
        }
    return {"schedulables": schedulables}


def _build_schedule_source_map(
    *,
    config: ProjectConfig,
    structure: ScheduleStructure,
) -> dict[str, Any]:
    if config.source_notebook is None:
        source_map = _extract_schedule_source_map(config.schedule_file, config.root)
        schedulables = source_map.setdefault("schedulables", {})
        for row in structure.operations:
            source = row.get("source")
            if not isinstance(source, Mapping):
                continue
            location = _file_source_location(source=source, config=config)
            schedulables[row["id"]] = location
            schedulables[row["operation_id"]] = location
        for block in structure.control_flow_blocks:
            source = block.get("source")
            if not isinstance(source, Mapping):
                continue
            location = _file_source_location(source=source, config=config)
            schedulables[block["id"]] = location
            schedulables[block["schedulable_id"]] = location
        return source_map

    primary: dict[str, Any] = {
        "kind": "notebook",
        "file": _relative_or_absolute(config.source_notebook, config.root),
    }
    if config.schedule_file is not None:
        primary["generated_file"] = _relative_or_absolute(config.schedule_file, config.root)
    source_map: dict[str, Any] = {"primary": primary, "schedulables": {}}

    mapper_cache: dict[Path, GeneratedLineMapper | None] = {}
    for row in structure.operations:
        source = row.get("source")
        if isinstance(source, Mapping):
            location = _notebook_source_location(
                source=source,
                config=config,
                mapper_cache=mapper_cache,
            )
            source_map["schedulables"][row["id"]] = location
            source_map["schedulables"][row["operation_id"]] = location

    for block in structure.control_flow_blocks:
        source = block.get("source")
        if isinstance(source, Mapping):
            location = _notebook_source_location(
                source=source,
                config=config,
                mapper_cache=mapper_cache,
            )
            source_map["schedulables"][block["id"]] = location
            source_map["schedulables"][block["schedulable_id"]] = location

    return source_map


def _file_source_location(*, source: Mapping[str, Any], config: ProjectConfig) -> dict[str, Any]:
    file_value = source.get("file")
    file_path = Path(str(file_value)) if file_value else (config.schedule_file or config.project_file)
    return {
        "file": _relative_or_absolute(file_path, config.root),
        "line": int(source.get("line", 1)) if isinstance(source.get("line"), int | float) else 1,
        "column": int(source.get("column", 0)) if isinstance(source.get("column"), int | float) else 0,
        **({"label": str(source["label"])} if isinstance(source.get("label"), str) else {}),
    }


def _notebook_source_location(
    *,
    source: Mapping[str, Any],
    config: ProjectConfig,
    mapper_cache: dict[Path, GeneratedLineMapper | None],
) -> dict[str, Any]:
    assert config.source_notebook is not None
    generated_line = int(source.get("line", 1)) if isinstance(source.get("line"), int | float) else 1
    location: dict[str, Any] = {
        "kind": "notebook",
        "file": _relative_or_absolute(config.source_notebook, config.root),
        "line": 1,
    }
    if isinstance(source.get("column"), int):
        location["column"] = source["column"]
    if isinstance(source.get("label"), str):
        location["label"] = source["label"]

    direct_notebook_location = _direct_notebook_source_location(
        source.get("file"),
        generated_line=generated_line,
        config=config,
    )
    if direct_notebook_location is not None:
        location["notebook"] = direct_notebook_location
        return location

    generated_file = _source_file_path(source.get("file"), config.root)
    if generated_file is not None:
        location["generated_file"] = _relative_or_absolute(generated_file, config.root)
        location["generated_line"] = generated_line

    notebook_location = None
    if generated_file is not None:
        mapper = _generated_line_mapper(generated_file, mapper_cache)
        if mapper is not None:
            notebook_location = mapper.location_for_generated_line(generated_line)
    if notebook_location is not None:
        location["notebook"] = {
            "file": _relative_or_absolute(config.source_notebook, config.root),
            "cell_index": notebook_location.cell_index,
            "cell_line": notebook_location.cell_line,
        }
    else:
        location["notebook"] = {
            "file": _relative_or_absolute(config.source_notebook, config.root),
            "cell_index": 0,
        }
    return location


def _direct_notebook_source_location(
    value: Any,
    *,
    generated_line: int,
    config: ProjectConfig,
) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    match = re.match(r"^(?P<file>.+\.ipynb)#cell-(?P<cell>[0-9]+)$", value)
    if match is None:
        return None
    notebook_path = Path(match.group("file")).resolve()
    if notebook_path != config.source_notebook:
        return None
    cell_index = int(match.group("cell"))
    notebook_location: dict[str, Any] = {
        "file": _relative_or_absolute(config.source_notebook, config.root),
        "cell_index": cell_index,
        "cell_line": generated_line,
    }
    try:
        cell = next(
            cell
            for cell in load_notebook_code_cells(config.source_notebook)
            if cell.cell_index == cell_index
        )
    except (OSError, StopIteration, ValueError):
        return notebook_location
    if cell.cell_id is not None:
        notebook_location["cell_id"] = cell.cell_id
    return notebook_location


def _generated_line_mapper(
    generated_file: Path,
    mapper_cache: dict[Path, GeneratedLineMapper | None],
) -> GeneratedLineMapper | None:
    if generated_file not in mapper_cache:
        if generated_file.name != "notebook_cells.py":
            mapper_cache[generated_file] = None
        else:
            try:
                mapper_cache[generated_file] = GeneratedLineMapper.from_source(
                    generated_file.read_text(encoding="utf-8"),
                    file=generated_file,
                )
            except OSError:
                mapper_cache[generated_file] = None
    return mapper_cache[generated_file]


def _source_file_path(value: Any, project_root: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _literal_keyword_string(call: ast.Call, name: str) -> str | None:
    for keyword in call.keywords:
        if keyword.arg != name:
            continue
        value = keyword.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return None


def extract_q1asm_programs(compiled_instructions: Any) -> list[Q1ASMProgram]:
    data = _unwrap_user_dict(compiled_instructions)
    programs: list[Q1ASMProgram] = []
    _collect_q1asm_programs(data, path=(), programs=programs)
    return programs


def _collect_q1asm_programs(value: Any, *, path: tuple[str, ...], programs: list[Q1ASMProgram]) -> None:
    value = _unwrap_user_dict(value)
    if not isinstance(value, dict):
        return

    sequencers = value.get("sequencers")
    if isinstance(sequencers, dict):
        for seq_name, settings in sequencers.items():
            program = _program_from_sequencer_settings(settings)
            if program is None:
                continue
            sequencer_path = (*path, str(seq_name))
            sequencer_id = _unique_sequencer_id(
                _safe_id("_".join(sequencer_path)),
                {candidate.sequencer_id for candidate in programs},
            )
            programs.append(
                Q1ASMProgram(
                    sequencer_id=sequencer_id,
                    relative_file=Path("q1asm") / f"{sequencer_id}.q1asm",
                    program=program,
                    path=sequencer_path,
                )
            )

    for key, child in value.items():
        if key == "sequencers":
            continue
        if isinstance(_unwrap_user_dict(child), dict):
            _collect_q1asm_programs(child, path=(*path, str(key)), programs=programs)


def _program_from_sequencer_settings(settings: Any) -> str | None:
    settings = _unwrap_user_dict(settings)
    sequence = settings.get("sequence") if isinstance(settings, dict) else getattr(settings, "sequence", None)
    sequence = _unwrap_user_dict(sequence)
    if not isinstance(sequence, dict):
        return None
    program = sequence.get("program")
    return program if isinstance(program, str) else None


def _write_q1asm_files(output_dir: Path, programs: list[Q1ASMProgram]) -> None:
    q1asm_dir = output_dir / "q1asm"
    if q1asm_dir.exists():
        for stale_file in q1asm_dir.glob("*.q1asm"):
            stale_file.unlink(missing_ok=True)
    for program in programs:
        path = output_dir / program.relative_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(program.program, encoding="utf-8")


def _valid_q1asm_provenance_rows(
    rows: list[dict[str, Any]],
    q1asm_programs: list[Q1ASMProgram],
) -> list[dict[str, Any]]:
    line_counts = {
        program.sequencer_id: len(program.program.splitlines())
        for program in q1asm_programs
    }
    ambiguous_base_ids = _ambiguous_collision_base_ids(q1asm_programs)
    valid_rows: list[dict[str, Any]] = []
    for row in rows:
        sequencer_id = str(row.get("sequencer_id") or row.get("sequencer") or "")
        if sequencer_id in ambiguous_base_ids:
            continue
        start_line = row.get("q1asm_line_start")
        end_line = row.get("q1asm_line_end")
        line_count = line_counts.get(sequencer_id)
        if not isinstance(start_line, int) or not isinstance(end_line, int) or line_count is None:
            continue
        if start_line < 1 or end_line < start_line or end_line > line_count:
            continue
        filtered_row = dict(row)
        filtered_row["operand_mappings"] = _valid_operand_mappings(
            row.get("operand_mappings", []),
            start_line=start_line,
            end_line=end_line,
            line_count=line_count,
        )
        valid_rows.append(filtered_row)
    return valid_rows


def _valid_operand_mappings(
    mappings: Any,
    *,
    start_line: int,
    end_line: int,
    line_count: int,
) -> list[dict[str, Any]]:
    if not isinstance(mappings, list):
        return []
    valid: list[dict[str, Any]] = []
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        line = mapping.get("line")
        line_end = mapping.get("line_end", line)
        if not isinstance(line, int) or not isinstance(line_end, int):
            continue
        if line < start_line or line_end < line or line_end > end_line or line_end > line_count:
            continue
        valid.append(mapping)
    return valid


def _ambiguous_collision_base_ids(q1asm_programs: list[Q1ASMProgram]) -> set[str]:
    ambiguous: set[str] = set()
    for program in q1asm_programs:
        natural_id = _safe_id("_".join(program.path))
        if program.sequencer_id != natural_id and re.fullmatch(rf"{re.escape(natural_id)}_[2-9][0-9]*", program.sequencer_id):
            ambiguous.add(natural_id)
    return ambiguous


def _load_python_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"qbstimeline_project_{abs(hash(path))}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load schedule module: {path}")
    module = importlib.util.module_from_spec(spec)
    source_root = str(path.parent.resolve())
    inserted = source_root not in sys.path
    if inserted:
        sys.path.insert(0, source_root)
    try:
        spec.loader.exec_module(module)
    finally:
        if inserted:
            try:
                sys.path.remove(source_root)
            except ValueError:
                pass
    return module


def _entrypoint(module: ModuleType, name: str):
    value = getattr(module, name, None)
    if not callable(value):
        raise RuntimeError(f"Schedule module does not define callable entrypoint '{name}'")
    return value


class _CompilerAdapter:
    def __init__(self, target: Any) -> None:
        self._target = target

    def compile(self, schedule: Any) -> Any:
        compile_method = getattr(self._target, "compile", None)
        if callable(compile_method):
            return compile_method(schedule)
        raise RuntimeError(f"{type(self._target).__name__} does not provide compile(schedule)")


def _compiler_from_value(value: Any) -> Any:
    if callable(getattr(value, "compile", None)):
        return value
    return _CompilerAdapter(value)


def _merge_object_source_trace(source_trace: SourceTrace, schedule: Any) -> None:
    raw_trace = _safe_getattr(schedule, "_qbstimeline_source_trace")
    if isinstance(raw_trace, SourceTrace):
        source_trace.locations_by_schedulable_id.update(raw_trace.locations_by_schedulable_id)
        source_trace.locations_by_operation_id.update(raw_trace.locations_by_operation_id)
        source_trace.locations_by_schedule_id.update(raw_trace.locations_by_schedule_id)
        return
    if not isinstance(raw_trace, Mapping):
        return
    schedulables = _get_value(schedule, "schedulables", {})
    operations = _get_value(schedule, "operations", None)
    if operations is None:
        operations = _get_value(schedule, "operation_dict", {})
    for key, raw_location in raw_trace.items():
        location = _source_trace_location_from_value(raw_location)
        if location is None:
            continue
        key_text = str(key)
        if isinstance(schedulables, Mapping) and key_text in schedulables:
            source_trace.locations_by_schedulable_id[key_text] = location
        elif isinstance(operations, Mapping) and key_text in operations:
            source_trace.locations_by_operation_id[key_text] = location
        else:
            source_trace.locations_by_schedulable_id[key_text] = location


def _merge_same_source_operand_inference(
    rows: list[dict[str, Any]],
    symbolic_blocks: list[dict[str, Any]],
    q1asm_programs: list[Q1ASMProgram],
) -> list[dict[str, Any]]:
    blocks_by_id = {str(block.get("id")): block for block in symbolic_blocks}
    merged_rows: list[dict[str, Any]] = []
    for row in rows:
        merged_row = dict(row)
        source_id = str(row.get("source_id") or "")
        block = blocks_by_id.get(source_id)
        if (
            block is not None
            and _missing_symbolic_operand_value_ids(row.get("operand_mappings"), block)
        ):
            row_range = _line_range(row)
            inferred_rows = infer_q1asm_provenance(
                [block],
                _q1asm_programs_for_row(row, q1asm_programs),
                reserved_q1asm_ranges=[candidate for candidate in rows if str(candidate.get("source_id") or "") != source_id],
            )
            inferred_match = next(
                (
                    candidate
                    for candidate in inferred_rows
                    if (candidate_range := _line_range(candidate)) is not None
                    if str(candidate.get("source_id") or "") == source_id
                    and candidate.get("sequencer_id") == row.get("sequencer_id")
                    and row_range is not None
                    and _ranges_are_compatible(candidate_range, row_range)
                    and candidate.get("operand_mappings")
                ),
                None,
            )
            if inferred_match is not None:
                merged_row["operand_mappings"] = [
                    *(row.get("operand_mappings") if isinstance(row.get("operand_mappings"), list) else []),
                    *_missing_operand_mappings(row.get("operand_mappings"), inferred_match["operand_mappings"]),
                ]
                if row_range is not None and not _operand_mappings_within_range(merged_row["operand_mappings"], *row_range):
                    merged_row["q1asm_line_start"] = inferred_match["q1asm_line_start"]
                    merged_row["q1asm_line_end"] = inferred_match["q1asm_line_end"]
                    merged_row["instruction_roles"] = inferred_match["instruction_roles"]
        merged_rows.append(merged_row)
    return merged_rows


def _q1asm_programs_for_row(
    row: Mapping[str, Any],
    q1asm_programs: list[Q1ASMProgram],
) -> list[Q1ASMProgram]:
    sequencer_id = str(row.get("sequencer_id") or row.get("sequencer") or "")
    if not sequencer_id:
        return q1asm_programs
    matching = [
        program
        for program in q1asm_programs
        if str(getattr(program, "sequencer_id", "")) == sequencer_id
    ]
    return matching or q1asm_programs


def _missing_symbolic_operand_value_ids(mappings: Any, block: dict[str, Any]) -> set[str]:
    expected = _symbolic_operand_value_ids(block)
    if not expected:
        return set()
    existing_mappings = mappings if isinstance(mappings, list) else []
    existing = {
        str(mapping.get("source_value_id"))
        for mapping in existing_mappings
        if isinstance(mapping, Mapping)
        and isinstance(mapping.get("source_value_id"), str)
    }
    return expected - existing


def _symbolic_operand_value_ids(block: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    duration_value_id = block.get("duration_value_id")
    if isinstance(duration_value_id, str):
        ids.add(duration_value_id)
    parameter_value_ids = block.get("parameter_value_ids")
    if isinstance(parameter_value_ids, Mapping):
        ids.update(str(value) for value in parameter_value_ids.values() if isinstance(value, str))
    return ids


def _missing_operand_mappings(existing: Any, inferred: Any) -> list[dict[str, Any]]:
    if not isinstance(inferred, list):
        return []
    existing_list = existing if isinstance(existing, list) else []
    existing_keys = {
        _operand_mapping_key(mapping)
        for mapping in existing_list
        if isinstance(mapping, Mapping)
    }
    return [
        mapping
        for mapping in inferred
        if isinstance(mapping, dict) and _operand_mapping_key(mapping) not in existing_keys
    ]


def _operand_mapping_key(mapping: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        mapping.get("line"),
        mapping.get("line_end", mapping.get("line")),
        mapping.get("instruction"),
        mapping.get("operand_index"),
        mapping.get("role"),
        mapping.get("source_value_id"),
    )


def _line_range(row: Mapping[str, Any]) -> tuple[int, int] | None:
    start = row.get("q1asm_line_start")
    end = row.get("q1asm_line_end")
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
        return None
    return start, end


def _operand_mappings_within_range(mappings: Any, start_line: int, end_line: int) -> bool:
    if not isinstance(mappings, list):
        return False
    for mapping in mappings:
        if not isinstance(mapping, Mapping):
            return False
        line = mapping.get("line")
        line_end = mapping.get("line_end", line)
        if not isinstance(line, int) or not isinstance(line_end, int):
            return False
        if line < start_line or line_end < line or line_end > end_line:
            return False
    return True


def _range_contains_range(outer_start: int, outer_end: int, inner_start: int, inner_end: int) -> bool:
    return outer_start <= inner_start <= inner_end <= outer_end


def _ranges_are_compatible(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return _range_contains_range(*first, *second) or _range_contains_range(*second, *first)


def _source_trace_location_from_value(value: Any) -> SourceTraceLocation | None:
    if isinstance(value, SourceTraceLocation):
        return value
    if not isinstance(value, Mapping):
        return None
    file = value.get("file")
    line = value.get("line")
    if not isinstance(file, str) or not isinstance(line, int | float):
        return None
    column = value.get("column", 0)
    label = value.get("label")
    return SourceTraceLocation(
        file=file,
        line=int(line),
        column=int(column) if isinstance(column, int | float) else 0,
        label=label if isinstance(label, str) else None,
    )


def _extract_operations(compiled_schedule: Any) -> list[dict[str, Any]]:
    return _extract_schedule_structure(compiled_schedule).operations


def _extract_schedule_structure(
    compiled_schedule: Any,
    *,
    source_trace: SourceTrace | None = None,
) -> ScheduleStructure:
    operations: list[dict[str, Any]] = []
    control_flow_blocks: list[dict[str, Any]] = []
    _collect_schedule_operations(
        compiled_schedule,
        operations=operations,
        control_flow_blocks=control_flow_blocks,
        id_prefix="",
        time_offset=0.0,
        parent_control_flow_id=None,
        depth=0,
        source_trace=source_trace,
    )
    _wrap_schedule_repetitions(
        compiled_schedule,
        operations=operations,
        control_flow_blocks=control_flow_blocks,
        source_trace=source_trace,
    )
    return ScheduleStructure(operations=operations, control_flow_blocks=control_flow_blocks)


def _source_location_dict(location: SourceTraceLocation) -> dict[str, Any]:
    source: dict[str, Any] = {
        "file": location.file,
        "line": location.line,
        "column": location.column,
    }
    if location.label is not None:
        source["label"] = location.label
    return source


def _attach_row_source(
    row: dict[str, Any],
    *,
    source_trace: SourceTrace | None,
    schedulable_id: str,
    row_id: str,
    operation_id: str,
) -> None:
    if source_trace is None:
        return
    location = (
        source_trace.locations_by_schedulable_id.get(row_id)
        or source_trace.locations_by_schedulable_id.get(schedulable_id)
        or source_trace.locations_by_operation_id.get(operation_id)
    )
    if location is None:
        return
    row["source"] = _source_location_dict(location)


def _collect_schedule_operations(
    schedule: Any,
    *,
    operations: list[dict[str, Any]],
    control_flow_blocks: list[dict[str, Any]],
    id_prefix: str,
    time_offset: float,
    parent_control_flow_id: str | None,
    depth: int,
    source_trace: SourceTrace | None,
) -> None:
    schedulables = _get_value(schedule, "schedulables", {})
    operation_lookup = _get_value(schedule, "operations", None)
    if operation_lookup is None:
        operation_lookup = _get_value(schedule, "operation_dict", {})
    has_experiments = isinstance(_safe_getattr(schedule, "_experiments"), list)
    if not isinstance(schedulables, Mapping) or (not schedulables and has_experiments):
        _collect_experiment_operations(
            schedule,
            operations=operations,
            control_flow_blocks=control_flow_blocks,
            id_prefix=id_prefix,
            time_offset=time_offset,
            parent_control_flow_id=parent_control_flow_id,
            depth=depth,
            source_trace=source_trace,
        )
        return
    if isinstance(operation_lookup, Mapping):
        manual_sweep = _manual_sweep_info(schedulables, operation_lookup)
        if manual_sweep is not None:
            _collect_manual_sweep(
                manual_sweep,
                operations=operations,
                control_flow_blocks=control_flow_blocks,
                id_prefix=id_prefix,
                time_offset=time_offset,
                parent_control_flow_id=parent_control_flow_id,
                depth=depth,
                source_trace=source_trace,
            )
            return

    use_source_order_timing = _uses_source_order_timing(schedulables)
    source_order_cursor = 0.0
    for schedulable_id, schedulable in schedulables.items():
        schedulable = _unwrap_user_dict(schedulable)
        if not isinstance(schedulable, Mapping):
            continue
        operation_id = schedulable.get("operation_id")
        operation = operation_lookup.get(operation_id) if isinstance(operation_lookup, Mapping) else None
        local_abs_time = _schedulable_abs_time(schedulable)
        if local_abs_time is None:
            local_abs_time = source_order_cursor if use_source_order_timing else 0.0
        abs_time = _clean_float(time_offset + local_abs_time)
        row_id = f"{id_prefix}{schedulable_id}"
        if _is_schedule_like(operation):
            _collect_schedule_operations(
                operation,
                operations=operations,
                control_flow_blocks=control_flow_blocks,
                id_prefix=f"{row_id}/",
                time_offset=abs_time,
                parent_control_flow_id=parent_control_flow_id,
                depth=depth,
                source_trace=source_trace,
            )
            source_order_cursor = max(source_order_cursor, local_abs_time + _schedule_preview_duration(operation))
            continue
        operation_duration = _operation_duration(operation)
        row = {
            "id": str(row_id),
            "operation_id": str(operation_id),
            "label": _operation_label(operation, operation_id),
            "abs_time": abs_time,
            "duration": operation_duration,
        }
        if parent_control_flow_id:
            row["parent_control_flow_id"] = parent_control_flow_id
            row["depth"] = depth
        _attach_row_source(
            row,
            source_trace=source_trace,
            schedulable_id=str(schedulable_id),
            row_id=str(row_id),
            operation_id=str(operation_id),
        )
        operations.append(row)

        control_flow_info = _control_flow_info(operation)
        body = _control_flow_body(operation, control_flow_info)
        if body is not None:
            body_duration = _schedule_preview_duration(body)
            item_duration = operation_duration or body_duration
            control_flow_id = f"control-flow:{row_id}"
            block = _control_flow_block(
                control_flow_id=control_flow_id,
                schedulable_id=str(row_id),
                operation_id=str(operation_id),
                operation=operation,
                control_flow_info=control_flow_info,
                abs_time=abs_time,
                body=body,
                parent_control_flow_id=parent_control_flow_id,
                depth=depth,
            )
            if "source" in row:
                block["source"] = row["source"]
            control_flow_blocks.append(block)
            _collect_schedule_operations(
                body,
                operations=operations,
                control_flow_blocks=control_flow_blocks,
                id_prefix=f"{row_id}/",
                time_offset=_clean_float(abs_time + _number_or_zero(_mapping_get(control_flow_info, "t0"))),
                parent_control_flow_id=control_flow_id,
                depth=depth + 1,
                source_trace=source_trace,
            )
            source_order_cursor = max(source_order_cursor, local_abs_time + item_duration)
            continue

        nested_body = _nested_schedule_body(operation)
        if nested_body is not None:
            nested_duration = _schedule_preview_duration(nested_body)
            _collect_schedule_operations(
                nested_body,
                operations=operations,
                control_flow_blocks=control_flow_blocks,
                id_prefix=f"{row_id}/",
                time_offset=abs_time,
                parent_control_flow_id=parent_control_flow_id,
                depth=depth,
                source_trace=source_trace,
            )
            source_order_cursor = max(source_order_cursor, local_abs_time + (operation_duration or nested_duration))
            continue

        source_order_cursor = max(source_order_cursor, local_abs_time + operation_duration)


def _collect_manual_sweep(
    manual_sweep: dict[str, Any],
    *,
    operations: list[dict[str, Any]],
    control_flow_blocks: list[dict[str, Any]],
    id_prefix: str,
    time_offset: float,
    parent_control_flow_id: str | None,
    depth: int,
    source_trace: SourceTrace | None,
) -> None:
    schedulable_id = str(manual_sweep["schedulable_id"])
    schedulable = manual_sweep["schedulable"]
    operation_id = manual_sweep["operation_id"]
    operation = manual_sweep["operation"]
    abs_time = _clean_float(time_offset + (_schedulable_abs_time(schedulable) or 0.0))
    row_id = f"{id_prefix}{schedulable_id}"
    nested_body = _nested_schedule_body(operation)
    operation_duration = _operation_duration(operation) or (
        _schedule_preview_duration(nested_body) if nested_body is not None else 0.0
    )
    row = {
        "id": str(row_id),
        "operation_id": str(operation_id),
        "label": _operation_label(operation, operation_id),
        "abs_time": abs_time,
        "duration": operation_duration,
    }
    if parent_control_flow_id:
        row["parent_control_flow_id"] = parent_control_flow_id
        row["depth"] = depth
    _attach_row_source(
        row,
        source_trace=source_trace,
        schedulable_id=schedulable_id,
        row_id=str(row_id),
        operation_id=str(operation_id),
    )
    operations.append(row)

    control_flow_id = f"control-flow:{row_id}"
    control_flow_blocks.append(
        {
            "id": control_flow_id,
            "kind": "sweep",
            "label": f"Sweep x{manual_sweep['repetitions']}",
            "abs_time": abs_time,
            "duration": _manual_sweep_duration(manual_sweep),
            "duration_kind": "expanded",
            "preview_abs_time": abs_time,
            "preview_duration": operation_duration,
            "preview_kind": "first_iteration",
            "operation_id": str(operation_id),
            "schedulable_id": str(row_id),
            "repetitions": manual_sweep["repetitions"],
            "body_operation_count": _body_operation_count(nested_body) if nested_body is not None else 1,
        }
    )
    iteration = _manual_sweep_iteration(manual_sweep, source_trace)
    if iteration is not None:
        control_flow_blocks[-1]["iteration"] = iteration
    if parent_control_flow_id:
        control_flow_blocks[-1]["parent_control_flow_id"] = parent_control_flow_id
        control_flow_blocks[-1]["depth"] = depth
    if "source" in row:
        control_flow_blocks[-1]["source"] = row["source"]
    if nested_body is not None:
        _collect_schedule_operations(
            nested_body,
            operations=operations,
            control_flow_blocks=control_flow_blocks,
            id_prefix=f"{row_id}/",
            time_offset=abs_time,
            parent_control_flow_id=control_flow_id,
            depth=depth + 1,
            source_trace=source_trace,
        )


def _wrap_schedule_repetitions(
    schedule: Any,
    *,
    operations: list[dict[str, Any]],
    control_flow_blocks: list[dict[str, Any]],
    source_trace: SourceTrace | None = None,
) -> None:
    repetitions = _schedule_repetitions(schedule)
    if repetitions <= 1 or not operations:
        return
    control_flow_id = f"control-flow:{SCHEDULE_REPETITION_ID}"
    for operation in operations:
        if operation.get("parent_control_flow_id"):
            operation["depth"] = int(operation.get("depth", 0)) + 1
        else:
            operation["parent_control_flow_id"] = control_flow_id
            operation["depth"] = 1
    for block in control_flow_blocks:
        if block.get("parent_control_flow_id"):
            block["depth"] = int(block.get("depth", 0)) + 1
        else:
            block["parent_control_flow_id"] = control_flow_id
            block["depth"] = 1
    block = {
        "id": control_flow_id,
        "kind": "loop",
        "label": _control_flow_label("loop", repetitions),
        "abs_time": 0.0,
        "duration": _structure_duration(operations, control_flow_blocks),
        "duration_kind": "expanded",
        "preview_abs_time": 0.0,
        "preview_duration": _structure_preview_duration(operations, control_flow_blocks),
        "preview_kind": "first_iteration",
        "operation_id": SCHEDULE_REPETITION_ID,
        "schedulable_id": SCHEDULE_REPETITION_ID,
        "repetitions": repetitions,
        "body_operation_count": len(operations),
        "iteration": {
            "kind": "schedule_repetition",
            "variable": "repetitions",
            "count": repetitions,
        },
    }
    if source_trace is not None:
        location = (
            source_trace.locations_by_schedule_id.get(id(schedule))
            or source_trace.locations_by_schedulable_id.get(SCHEDULE_REPETITION_ID)
        )
        if location is not None:
            block["source"] = _source_location_dict(location)
    control_flow_blocks.insert(0, block)


def _structure_duration(
    operations: list[dict[str, Any]],
    control_flow_blocks: list[dict[str, Any]],
) -> float:
    end = 0.0
    for row in [*operations, *control_flow_blocks]:
        end = max(end, _number_or_zero(row.get("abs_time")) + _number_or_zero(row.get("duration")))
    return _clean_float(end)


def _structure_preview_duration(
    operations: list[dict[str, Any]],
    control_flow_blocks: list[dict[str, Any]],
) -> float:
    end = 0.0
    for row in [*operations, *control_flow_blocks]:
        start = _number_or_zero(row.get("preview_abs_time", row.get("abs_time")))
        duration = _number_or_zero(row.get("preview_duration", row.get("duration")))
        end = max(end, start + duration)
    return _clean_float(end)


def _manual_sweep_info(
    schedulables: Mapping[Any, Any],
    operation_lookup: Mapping[Any, Any],
) -> dict[str, Any] | None:
    if len(schedulables) < MANUAL_SWEEP_COMPACT_MIN:
        return None

    first: dict[str, Any] | None = None
    labels: set[str] = set()
    signatures: set[tuple[Any, ...]] = set()
    abs_times: list[float] = []
    schedulable_ids: list[str] = []
    for schedulable_id, schedulable in schedulables.items():
        schedulable_ids.append(str(schedulable_id))
        schedulable = _unwrap_user_dict(schedulable)
        if not isinstance(schedulable, Mapping):
            return None
        local_abs_time = _schedulable_abs_time(schedulable)
        if local_abs_time is not None:
            abs_times.append(local_abs_time)
        operation_id = schedulable.get("operation_id")
        operation = operation_lookup.get(operation_id)
        if _has_control_flow_metadata(_control_flow_info(operation)):
            return None
        nested_body = _nested_schedule_body(operation)
        if nested_body is None:
            return None
        signature = _schedule_shape_signature(nested_body)
        if signature is None:
            return None
        signatures.add(signature)
        label = _operation_label(operation, operation_id)
        labels.add(label)
        if first is None:
            first = {
                "schedulable_id": schedulable_id,
                "schedulable": schedulable,
                "operation_id": operation_id,
                "operation": operation,
            }
        if len(labels) > 1:
            return None
        if len(signatures) > 1:
            return None

    if first is None:
            return None
    if abs_times:
        if len(abs_times) != len(schedulables):
            return None
        first_abs_time = abs_times[0]
        if any(abs(abs_time - first_abs_time) > 1e-15 for abs_time in abs_times[1:]):
            return None
    first["repetitions"] = len(schedulables)
    first["schedulable_ids"] = schedulable_ids
    return first


def _manual_sweep_iteration(
    manual_sweep: Mapping[str, Any],
    source_trace: SourceTrace | None,
) -> dict[str, Any] | None:
    if source_trace is None:
        return None
    schedulable_ids = manual_sweep.get("schedulable_ids")
    if not isinstance(schedulable_ids, list) or not schedulable_ids:
        return None
    iterations: list[dict[str, Any]] = []
    for schedulable_id in schedulable_ids:
        location = source_trace.locations_by_schedulable_id.get(str(schedulable_id))
        if location is None:
            return None
        iteration = _for_iteration_from_source_location(location)
        if iteration is None:
            return None
        iterations.append(iteration)
    first = iterations[0]
    if any(iteration != first for iteration in iterations[1:]):
        return None
    repetitions = manual_sweep.get("repetitions")
    if isinstance(repetitions, int | float):
        return {**first, "count": int(repetitions)}
    return first


def _for_iteration_from_source_location(location: SourceTraceLocation) -> dict[str, Any] | None:
    path = Path(location.file)
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return None
    enclosing_for_nodes: list[ast.For] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        end_lineno = getattr(node, "end_lineno", None) or node.lineno
        if node.lineno <= location.line <= end_lineno:
            enclosing_for_nodes.append(node)
    if len(enclosing_for_nodes) != 1:
        return None
    loop = enclosing_for_nodes[0]
    if not isinstance(loop.target, ast.Name):
        return None
    source_expr = _unparse_ast(loop.iter)
    if not source_expr:
        return None
    return {
        "kind": "manual_sweep",
        "variable": loop.target.id,
        "source": source_expr,
    }


def _unparse_ast(node: ast.AST) -> str | None:
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _schedule_shape_signature(schedule: Any) -> tuple[Any, ...] | None:
    schedulables = _get_value(schedule, "schedulables", {})
    operation_lookup = _get_value(schedule, "operations", None)
    if operation_lookup is None:
        operation_lookup = _get_value(schedule, "operation_dict", {})
    if not isinstance(schedulables, Mapping) or not isinstance(operation_lookup, Mapping):
        return None
    items: list[Any] = []
    for schedulable in schedulables.values():
        schedulable = _unwrap_user_dict(schedulable)
        if not isinstance(schedulable, Mapping):
            return None
        operation_id = schedulable.get("operation_id")
        operation = operation_lookup.get(operation_id)
        nested_body = _nested_schedule_body(operation)
        if nested_body is not None:
            nested_signature = _schedule_shape_signature(nested_body)
            if nested_signature is None:
                return None
            items.append(("schedule", _operation_label(operation, operation_id), nested_signature))
        else:
            items.append(("operation", _operation_label(operation, operation_id), _operation_duration(operation)))
    return tuple(items)


def _manual_sweep_duration(manual_sweep: Mapping[str, Any]) -> float:
    repetitions = manual_sweep.get("repetitions")
    if not isinstance(repetitions, int | float) or repetitions <= 0:
        return 0.0
    operation = manual_sweep.get("operation")
    duration = _operation_duration(operation)
    nested_body = _nested_schedule_body(operation)
    if not duration and nested_body is not None:
        duration = _schedule_preview_duration(nested_body)
    return _clean_float(duration * repetitions)


def _schedule_preview_duration(schedule: Any) -> float:
    duration = _safe_getattr(schedule, "duration")
    if isinstance(duration, int | float):
        return _clean_float(float(duration))
    schedulables = _get_value(schedule, "schedulables", {})
    operation_lookup = _get_value(schedule, "operations", None)
    if operation_lookup is None:
        operation_lookup = _get_value(schedule, "operation_dict", {})
    if not isinstance(schedulables, Mapping) or not isinstance(operation_lookup, Mapping):
        return 0.0
    manual_sweep = _manual_sweep_info(schedulables, operation_lookup)
    if manual_sweep is not None:
        return _manual_sweep_duration(manual_sweep)

    use_source_order_timing = _uses_source_order_timing(schedulables)
    source_order_cursor = 0.0
    end = 0.0
    for schedulable in schedulables.values():
        schedulable = _unwrap_user_dict(schedulable)
        if not isinstance(schedulable, Mapping):
            continue
        operation = operation_lookup.get(schedulable.get("operation_id"))
        local_abs_time = _schedulable_abs_time(schedulable)
        if local_abs_time is None:
            local_abs_time = source_order_cursor if use_source_order_timing else 0.0
        item_duration = _operation_duration(operation)
        body = _control_flow_body(operation, _control_flow_info(operation))
        nested_body = body if body is not None else _nested_schedule_body(operation)
        if not item_duration and nested_body is not None:
            item_duration = _schedule_preview_duration(nested_body)
        end = max(end, local_abs_time + item_duration)
        source_order_cursor = max(source_order_cursor, local_abs_time + item_duration)
    return _clean_float(end)


def _uses_source_order_timing(schedulables: Mapping[Any, Any]) -> bool:
    rows = [_unwrap_user_dict(schedulable) for schedulable in schedulables.values()]
    mappings = [row for row in rows if isinstance(row, Mapping)]
    if not mappings:
        return False
    if any(_schedulable_abs_time(row) is not None for row in mappings):
        return False
    return len(mappings) > 1


def _schedulable_abs_time(schedulable: Mapping[str, Any]) -> float | None:
    value = schedulable.get("abs_time")
    return float(value) if isinstance(value, int | float) else None


def _schedule_repetitions(schedule: Any) -> int:
    repetitions = _get_value(schedule, "repetitions", 1)
    return int(repetitions) if isinstance(repetitions, int | float) and repetitions > 1 else 1


def _experiment_schedules(schedule: Any) -> list[Any]:
    experiments = _safe_getattr(schedule, "_experiments")
    if not isinstance(experiments, list):
        return []
    schedules: list[Any] = []
    for experiment in experiments:
        if not isinstance(experiment, Mapping):
            continue
        steps = experiment.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            schedule_info = step.get("schedule_info")
            if not isinstance(schedule_info, Mapping):
                continue
            nested_schedule = schedule_info.get("schedule")
            if nested_schedule is not None:
                schedules.append(nested_schedule)
    return schedules


def _collect_experiment_operations(
    schedule: Any,
    *,
    operations: list[dict[str, Any]],
    control_flow_blocks: list[dict[str, Any]],
    id_prefix: str,
    time_offset: float,
    parent_control_flow_id: str | None,
    depth: int,
    source_trace: SourceTrace | None,
) -> None:
    experiments = _safe_getattr(schedule, "_experiments")
    if not isinstance(experiments, list):
        return
    for experiment_index, experiment in enumerate(experiments):
        if not isinstance(experiment, Mapping):
            continue
        steps = experiment.get("steps")
        if not isinstance(steps, list):
            continue
        _collect_experiment_step_list(
            steps,
            operations=operations,
            control_flow_blocks=control_flow_blocks,
            id_prefix=f"{id_prefix}experiment{experiment_index}/",
            time_offset=time_offset,
            parent_control_flow_id=parent_control_flow_id,
            depth=depth,
            source_trace=source_trace,
        )


def _collect_experiment_step_list(
    steps: list[Any],
    *,
    operations: list[dict[str, Any]],
    control_flow_blocks: list[dict[str, Any]],
    id_prefix: str,
    time_offset: float,
    parent_control_flow_id: str | None,
    depth: int,
    source_trace: SourceTrace | None,
) -> None:
    schedule_step_count = sum(1 for step in steps if _step_schedule(step) is not None)
    for step_index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            continue
        loop_info = step.get("loop_info")
        if isinstance(loop_info, Mapping):
            _collect_experiment_loop_step(
                step,
                step_index=step_index,
                operations=operations,
                control_flow_blocks=control_flow_blocks,
                id_prefix=id_prefix,
                time_offset=time_offset,
                parent_control_flow_id=parent_control_flow_id,
                depth=depth,
                source_trace=source_trace,
            )
            continue
        nested_schedule = _step_schedule(step)
        if nested_schedule is not None:
            nested_prefix = id_prefix if schedule_step_count == 1 else f"{id_prefix}step{step_index}/"
            _collect_schedule_operations(
                nested_schedule,
                operations=operations,
                control_flow_blocks=control_flow_blocks,
                id_prefix=nested_prefix,
                time_offset=time_offset,
                parent_control_flow_id=parent_control_flow_id,
                depth=depth,
                source_trace=source_trace,
            )


def _collect_experiment_loop_step(
    step: Mapping[str, Any],
    *,
    step_index: int,
    operations: list[dict[str, Any]],
    control_flow_blocks: list[dict[str, Any]],
    id_prefix: str,
    time_offset: float,
    parent_control_flow_id: str | None,
    depth: int,
    source_trace: SourceTrace | None,
) -> None:
    loop_info = step.get("loop_info")
    if not isinstance(loop_info, Mapping):
        return
    row_id = f"{id_prefix}step{step_index}"
    label = str(step.get("name") or "Experiment loop")
    row = {
        "id": row_id,
        "operation_id": row_id,
        "label": label,
        "abs_time": time_offset,
        "duration": 0.0,
    }
    if parent_control_flow_id:
        row["parent_control_flow_id"] = parent_control_flow_id
        row["depth"] = depth
    _attach_row_source(
        row,
        source_trace=source_trace,
        schedulable_id=row_id,
        row_id=row_id,
        operation_id=row_id,
    )
    operations.append(row)
    repetitions = _domain_repetitions(loop_info.get("domains"))
    kind = _domain_kind(loop_info.get("domains"))
    control_flow_id = f"control-flow:{row_id}"
    block = {
        "id": control_flow_id,
        "kind": kind,
        "label": _control_flow_label(kind, repetitions),
        "abs_time": time_offset,
        "duration": 0.0,
        "operation_id": row_id,
        "schedulable_id": row_id,
        "repetitions": repetitions,
        "body_operation_count": _experiment_step_body_count(loop_info.get("steps")),
    }
    iteration = _domain_iteration(loop_info.get("domains"), repetitions)
    if iteration is not None:
        block["iteration"] = iteration
    control_flow_blocks.append(block)
    if parent_control_flow_id:
        control_flow_blocks[-1]["parent_control_flow_id"] = parent_control_flow_id
        control_flow_blocks[-1]["depth"] = depth
    if "source" in row:
        control_flow_blocks[-1]["source"] = row["source"]
    nested_steps = loop_info.get("steps")
    if isinstance(nested_steps, list):
        _collect_experiment_step_list(
            nested_steps,
            operations=operations,
            control_flow_blocks=control_flow_blocks,
            id_prefix=f"{row_id}/",
            time_offset=time_offset,
            parent_control_flow_id=control_flow_id,
            depth=depth + 1,
            source_trace=source_trace,
        )


def _step_schedule(step: Any) -> Any | None:
    if not isinstance(step, Mapping):
        return None
    schedule_info = step.get("schedule_info")
    if not isinstance(schedule_info, Mapping):
        return None
    return schedule_info.get("schedule")


def _domain_repetitions(domains: Any) -> int:
    if not isinstance(domains, Mapping):
        return 1
    repetitions = 1
    for domain in domains.values():
        size = _domain_value(domain, "num")
        if isinstance(size, int | float) and size > 0:
            repetitions *= int(size)
    return repetitions


def _domain_iteration(domains: Any, repetitions: Any) -> dict[str, Any] | None:
    if not isinstance(domains, Mapping) or not domains:
        return None
    variables = [
        variable
        for variable in (str(variable) for variable in domains.keys())
        if not _is_generated_qblox_variable(variable)
    ]
    iteration: dict[str, Any] = {
        "kind": "domain",
        "count": _iteration_count(repetitions, fallback=_domain_repetitions(domains)),
    }
    if variables:
        iteration["variable"] = ", ".join(variables)
    if len(variables) > 1:
        iteration["variables"] = variables
    return iteration


def _is_generated_qblox_variable(value: str) -> bool:
    return bool(GENERATED_QBLOX_VARIABLE_RE.fullmatch(value))


def _iteration_count(value: Any, *, fallback: int) -> int | float:
    if isinstance(value, int | float) and value > 0:
        return int(value) if float(value).is_integer() else value
    return fallback


def _domain_kind(domains: Any) -> str:
    if isinstance(domains, Mapping):
        for domain in domains.values():
            dtype = str(_domain_value(domain, "dtype") or "").lower()
            if dtype and "number" not in dtype:
                return "sweep"
    return "loop"


def _domain_value(domain: Any, key: str) -> Any:
    if isinstance(domain, Mapping):
        return domain.get(key)
    return getattr(domain, key, None)


def _experiment_step_body_count(steps: Any) -> int:
    if not isinstance(steps, list):
        return 0
    count = 0
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        if isinstance(step.get("loop_info"), Mapping):
            count += 1
            continue
        nested_schedule = _step_schedule(step)
        if nested_schedule is not None:
            count += _body_operation_count(nested_schedule)
    return count


def _control_flow_info(operation: Any) -> Mapping[str, Any]:
    info = _get_value(operation, "control_flow_info", {})
    return info if isinstance(info, Mapping) else {}


def _control_flow_body(operation: Any, control_flow_info: Mapping[str, Any]) -> Any | None:
    body = _mapping_get(control_flow_info, "body")
    if body is not None:
        return body
    if not _has_control_flow_metadata(control_flow_info):
        return None
    body = _safe_getattr(operation, "body")
    return body if body is not None else None


def _has_control_flow_metadata(control_flow_info: Mapping[str, Any]) -> bool:
    return any(key in control_flow_info for key in ("repetitions", "domain", "body", "t0"))


def _nested_schedule_body(operation: Any) -> Any | None:
    body = _safe_getattr(operation, "body")
    if _is_schedule_like(body):
        return body
    if _is_schedule_like(operation):
        return operation
    return None


def _is_schedule_like(value: Any) -> bool:
    if value is None:
        return False
    schedulables = _get_value(value, "schedulables", None)
    operation_lookup = _get_value(value, "operations", None)
    if operation_lookup is None:
        operation_lookup = _get_value(value, "operation_dict", None)
    return isinstance(schedulables, Mapping) and isinstance(operation_lookup, Mapping)


def _control_flow_block(
    *,
    control_flow_id: str,
    schedulable_id: str,
    operation_id: str,
    operation: Any,
    control_flow_info: Mapping[str, Any],
    abs_time: float,
    body: Any,
    parent_control_flow_id: str | None,
    depth: int,
) -> dict[str, Any]:
    repetitions = _control_flow_repetitions(control_flow_info)
    kind = _control_flow_kind(control_flow_info)
    t0 = _number_or_zero(_mapping_get(control_flow_info, "t0"))
    preview_duration = _schedule_preview_duration(body)
    duration = _operation_duration(operation)
    block = {
        "id": str(control_flow_id),
        "kind": kind,
        "label": _control_flow_label(kind, repetitions),
        "abs_time": abs_time,
        "duration": duration,
        "preview_abs_time": _clean_float(abs_time + t0),
        "preview_duration": preview_duration,
        "preview_kind": "first_iteration",
        "operation_id": operation_id,
        "schedulable_id": schedulable_id,
        "body_operation_count": _body_operation_count(body),
    }
    if duration != preview_duration:
        block["duration_kind"] = "expanded"
    iteration = _domain_iteration(_mapping_get(control_flow_info, "domain"), repetitions)
    if iteration is not None:
        block["iteration"] = iteration
    if parent_control_flow_id:
        block["parent_control_flow_id"] = parent_control_flow_id
        block["depth"] = depth
    if isinstance(repetitions, int | float):
        block["repetitions"] = repetitions
    return block


def _control_flow_kind(control_flow_info: Mapping[str, Any]) -> str:
    domain = _mapping_get(control_flow_info, "domain")
    if isinstance(domain, Mapping):
        for value in domain.values():
            dtype = str(_domain_value(value, "dtype") or "").lower()
            if dtype and "number" not in dtype:
                return "sweep"
    return "loop"


def _control_flow_repetitions(control_flow_info: Mapping[str, Any]) -> Any:
    repetitions = _mapping_get(control_flow_info, "repetitions")
    if isinstance(repetitions, int | float) and repetitions > 0:
        return repetitions
    domain_repetitions = _domain_repetitions(_mapping_get(control_flow_info, "domain"))
    if domain_repetitions > 1:
        return domain_repetitions
    return repetitions


def _control_flow_label(kind: str, repetitions: Any) -> str:
    prefix = "Sweep" if kind == "sweep" else "Loop"
    if isinstance(repetitions, int | float):
        return f"{prefix} x{_format_repetitions(repetitions)}"
    return prefix


def _format_repetitions(repetitions: int | float) -> str:
    return str(int(repetitions)) if float(repetitions).is_integer() else f"{repetitions:g}"


def _body_operation_count(body: Any) -> int:
    schedulables = _get_value(body, "schedulables", {})
    if not isinstance(schedulables, Mapping):
        return 0
    operation_lookup = _get_value(body, "operations", None)
    if operation_lookup is None:
        operation_lookup = _get_value(body, "operation_dict", {})
    if not isinstance(operation_lookup, Mapping):
        return len(schedulables)
    count = 0
    for schedulable in schedulables.values():
        schedulable = _unwrap_user_dict(schedulable)
        if not isinstance(schedulable, Mapping):
            continue
        operation = operation_lookup.get(schedulable.get("operation_id"))
        if _is_schedule_like(operation):
            count += _body_operation_count(operation)
        else:
            count += 1
    return count


def _mapping_get(mapping: Mapping[str, Any], key: str) -> Any:
    return mapping.get(key) if isinstance(mapping, Mapping) else None


def _number_or_zero(value: Any) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


def _clean_float(value: float) -> float:
    return float(f"{value:.15g}")


def _extract_timing_table(compiled_schedule: Any) -> list[dict[str, Any]]:
    try:
        timing_table = getattr(compiled_schedule, "timing_table", None)
    except Exception:
        return []
    table_data = getattr(timing_table, "data", None)
    if table_data is None:
        return []
    if hasattr(table_data, "to_dict"):
        records = table_data.to_dict(orient="records")
        return records if isinstance(records, list) else []
    return []


def _schedule_name(compiled_schedule: Any, schedule: Any) -> str:
    for candidate in (
        _get_value(compiled_schedule, "name", None),
        _get_value(schedule, "name", None),
        getattr(compiled_schedule, "name", None),
        getattr(schedule, "name", None),
    ):
        if isinstance(candidate, str):
            return candidate
    return "schedule"


def _operation_label(operation: Any, operation_id: Any) -> str:
    operation = _unwrap_user_dict(operation)
    if isinstance(operation, dict):
        name = operation.get("name")
        if isinstance(name, str):
            return name
    name = getattr(operation, "name", None)
    if isinstance(name, str):
        return name
    return str(operation_id)


def _operation_duration(operation: Any) -> Any:
    duration = _safe_getattr(operation, "duration")
    if not isinstance(duration, int | float):
        operation = _unwrap_user_dict(operation)
        if isinstance(operation, dict):
            duration = operation.get("duration")
        else:
            duration = _safe_getattr(operation, "duration")
    if not isinstance(duration, int | float):
        control_flow_info = _control_flow_info(operation)
        body = _control_flow_body(operation, control_flow_info)
        body_duration = _safe_getattr(body, "duration")
        repetitions = _mapping_get(control_flow_info, "repetitions")
        if isinstance(body_duration, int | float) and isinstance(repetitions, int | float):
            duration = body_duration * repetitions
    return duration if isinstance(duration, int | float) else 0.0


def _safe_getattr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return normalized or "seq"


def _unique_sequencer_id(base_id: str, existing_ids: set[str]) -> str:
    if base_id not in existing_ids:
        return base_id
    suffix = 2
    while f"{base_id}_{suffix}" in existing_ids:
        suffix += 1
    return f"{base_id}_{suffix}"
