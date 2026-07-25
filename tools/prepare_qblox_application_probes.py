from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, NamedTuple


DEFAULT_MANIFEST = Path("examples") / "qblox_application_examples" / "manifest.json"
DEFAULT_OUTPUT_DIR = Path(".scratch") / "qblox_application_probes"


class CompletedProbeRun(NamedTuple):
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


class NotebookCodeCell(NamedTuple):
    cell_index: int
    source: str


ProbeRunner = Callable[[Sequence[str]], CompletedProbeRun]


def prepare_probes(manifest_path: str | Path, output_dir: str | Path, *, clean: bool = False) -> dict[str, Any]:
    manifest_path = Path(manifest_path).resolve()
    examples_root = manifest_path.parent
    output_dir = Path(output_dir).resolve()
    if clean:
        _clean_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_manifest = _load_json(manifest_path)
    probes: list[dict[str, Any]] = []
    for notebook in _iter_notebook_entries(source_manifest, examples_root):
        probe = _prepare_probe(notebook, output_dir)
        probes.append(probe)

    generated_manifest = {
        "source_manifest": str(manifest_path),
        "output_dir": str(output_dir),
        "probe_count": len(probes),
        "ready_count": sum(1 for probe in probes if probe["analysis"]["schedule_candidates"]),
        "probes": probes,
    }
    (output_dir / "probe_manifest.json").write_text(
        json.dumps(generated_manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return generated_manifest


def run_probe_projects(
    probe_manifest_path: str | Path,
    *,
    timeout_seconds: int = 90,
    runner: Callable[..., CompletedProbeRun] | None = None,
) -> dict[str, Any]:
    probe_manifest_path = Path(probe_manifest_path).resolve()
    probe_manifest = _load_json(probe_manifest_path)
    runner = runner or _run_subprocess
    runs: list[dict[str, Any]] = []

    for probe in probe_manifest.get("probes", []):
        project_file = Path(probe["project_file"]).resolve()
        out_file = project_file.parent / ".qbs_timeline" / "qbs_ir.json"
        _remove_stale_outputs(project_file.parent)
        if probe.get("analysis", {}).get("schedule_candidates") == []:
            runs.append(
                {
                    "project_file": str(project_file),
                    "notebook": probe.get("notebook"),
                    "exit_code": 0,
                    "duration_seconds": 0.0,
                    "stdout": "",
                    "stderr": "",
                    "ir_path": str(out_file),
                    "skipped": True,
                    "skip_reason": "No Schedule(...) assignment or schedule factory detected.",
                }
            )
            continue
        command = [
            sys.executable,
            "-m",
            "qbstimeline",
            "analyze",
            "--project",
            str(project_file),
            "--out",
            str(out_file),
        ]
        result = runner(command, cwd=project_file.parent, timeout=timeout_seconds)
        runs.append(
            {
                "project_file": str(project_file),
                "notebook": probe.get("notebook"),
                "exit_code": result.exit_code,
                "duration_seconds": result.duration_seconds,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "ir_path": str(out_file),
            }
        )

    report = {
        "probe_manifest": str(probe_manifest_path),
        "run_count": len(runs),
        "ok_count": sum(1 for run in runs if run["exit_code"] == 0 and not run.get("skipped")),
        "skip_count": sum(1 for run in runs if run.get("skipped")),
        "fail_count": sum(1 for run in runs if run["exit_code"] != 0),
        "runs": runs,
    }
    (probe_manifest_path.parent / "probe_run_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def _remove_stale_outputs(project_dir: Path) -> None:
    timeline_dir = project_dir / ".qbs_timeline"
    for relative_path in (Path("qbs_ir.json"), Path("index.html")):
        path = timeline_dir / relative_path
        try:
            path.unlink()
        except FileNotFoundError:
            continue


def _prepare_probe(notebook: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    notebook_path = Path(notebook["path"]).resolve()
    code_cells = _read_code_cells(notebook_path)
    prepared_cells = _prepare_code_cells(code_cells)
    prepared_code = "\n\n".join(
        f"# %% qbstimeline notebook cell {cell.cell_index + 1}\n{cell.source}".rstrip()
        for cell in prepared_cells
    )
    analysis = _analyze_code(
        "\n\n".join(cell.source for cell in prepared_cells),
        code_cell_count=len(code_cells),
    )
    probe_dir = output_dir / _safe_probe_name(notebook["page_slug"], notebook_path.stem)
    probe_dir.mkdir(parents=True, exist_ok=True)

    (probe_dir / "notebook_cells.py").write_text(prepared_code + "\n", encoding="utf-8")
    (probe_dir / "schedule.py").write_text(
        _render_schedule_wrapper(
            notebook_dir=notebook_path.parent,
            schedule_candidates=analysis["schedule_candidates"],
            compiler_candidates=analysis["compiler_candidates"],
        ),
        encoding="utf-8",
    )
    (probe_dir / "qbstimeline.yml").write_text(
        _render_project_config(notebook_path=notebook_path),
        encoding="utf-8",
    )

    warnings = _warnings_for_analysis(analysis)
    metadata = {
        "page_slug": notebook["page_slug"],
        "page_title": notebook.get("page_title"),
        "page_url": notebook.get("page_url"),
        "notebook": str(notebook_path),
        "probe_dir": str(probe_dir),
        "project_file": str(probe_dir / "qbstimeline.yml"),
        "analysis": analysis,
        "warnings": warnings,
    }
    (probe_dir / "probe.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return metadata


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_notebook_entries(manifest: dict[str, Any], examples_root: Path) -> Iterable[dict[str, Any]]:
    for entry in manifest.get("entries", []):
        extracted_dir = entry.get("extracted_dir")
        if not isinstance(extracted_dir, str):
            continue
        for file_name in entry.get("files", []):
            if not isinstance(file_name, str) or not file_name.endswith(".ipynb"):
                continue
            yield {
                "page_slug": str(entry.get("page_slug", Path(file_name).stem)),
                "page_title": entry.get("page_title"),
                "page_url": entry.get("page_url"),
                "path": examples_root / extracted_dir / file_name,
            }


def _read_code_cells(notebook_path: Path) -> list[NotebookCodeCell]:
    notebook = _load_json(notebook_path)
    cells = []
    for index, cell in enumerate(notebook.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        if isinstance(source, list):
            cells.append(NotebookCodeCell(index, "".join(str(line) for line in source)))
        elif isinstance(source, str):
            cells.append(NotebookCodeCell(index, source))
    return cells


def _prepare_code_cells(cells: Sequence[NotebookCodeCell]) -> list[NotebookCodeCell]:
    prepared_cells: list[NotebookCodeCell] = []
    execution_boundary_seen = False
    for cell in cells:
        if execution_boundary_seen:
            prepared_cells.append(
                NotebookCodeCell(
                    cell.cell_index,
                    _comment_block(
                        "qbstimeline probe skipped cell after execution boundary",
                        _comment_ipython_lines(cell.source).rstrip(),
                    ),
                )
            )
            continue
        prepared, stopped = _prepare_code_cell(cell.source)
        prepared_cells.append(NotebookCodeCell(cell.cell_index, prepared))
        execution_boundary_seen = stopped
    prepared_sources = _comment_unused_hardware_accesses([cell.source for cell in prepared_cells])
    return [
        NotebookCodeCell(cell.cell_index, prepared_source)
        for cell, prepared_source in zip(prepared_cells, prepared_sources, strict=True)
    ]


def _prepare_code_cell(source: str) -> tuple[str, bool]:
    source = _comment_ipython_lines(source)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return (_comment_block("qbstimeline probe skipped non-Python notebook cell", source), False)

    kept: list[ast.stmt] = []
    skipped_after: ast.stmt | None = None
    for statement in tree.body:
        if _contains_execution_call(statement):
            skipped_after = statement
            break
        kept.append(statement)
    if skipped_after is None:
        return (source.rstrip(), False)
    if not kept:
        return (_comment_block("qbstimeline probe skipped execution-only notebook cell", source), True)
    prepared = ast.unparse(ast.Module(body=kept, type_ignores=[]))
    skipped = ast.get_source_segment(source, skipped_after) or "execution statement"
    return (
        prepared.rstrip()
        + "\n\n"
        + _comment_block("qbstimeline probe stopped before execution statement", skipped),
        True,
    )


def _comment_ipython_lines(source: str) -> str:
    lines = []
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("!", "%", "?")):
            lines.append("# qbstimeline probe skipped IPython line: " + line)
        elif re.search(r"\bconnect_clusters\s*\(", stripped):
            lines.append("# qbstimeline probe skipped live/dummy cluster connection during static analysis:")
            lines.append("# " + line)
        else:
            lines.append(line)
    return "\n".join(lines) + ("\n" if source.endswith("\n") else "")


def _comment_unused_hardware_accesses(cells: list[str]) -> list[str]:
    loaded_names_by_cell: list[set[str]] = []
    for cell in cells:
        try:
            tree = ast.parse(cell)
        except SyntaxError:
            loaded_names_by_cell.append(set())
            continue
        loaded_names_by_cell.append(
            {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
        )

    suffix_loaded: list[set[str]] = [set() for _ in cells]
    loaded_after: set[str] = set()
    for index in range(len(cells) - 1, -1, -1):
        suffix_loaded[index] = set(loaded_after)
        loaded_after.update(loaded_names_by_cell[index])

    return [
        _comment_unused_hardware_accesses_in_cell(cell, suffix_loaded[index])
        for index, cell in enumerate(cells)
    ]


def _comment_unused_hardware_accesses_in_cell(cell: str, loaded_after_cell: set[str]) -> str:
    try:
        tree = ast.parse(cell)
    except SyntaxError:
        return cell

    replacements: list[tuple[int, int, str]] = []
    loaded_later_in_cell = set(loaded_after_cell)
    for statement in reversed(tree.body):
        source = ast.get_source_segment(cell, statement)
        assigned_names = set(_assigned_names(statement))
        if (
            source
            and assigned_names
            and _contains_hardware_introspection(statement)
            and not (assigned_names & loaded_later_in_cell)
        ):
            replacements.append(
                (
                    statement.lineno,
                    getattr(statement, "end_lineno", statement.lineno),
                    _comment_block("qbstimeline probe skipped unused hardware introspection", source),
                )
            )
        elif (
            source
            and assigned_names
            and _contains_hardware_configuration_access(statement)
            and (assigned_names & loaded_later_in_cell)
        ):
            replacements.append(
                (
                    statement.lineno,
                    getattr(statement, "end_lineno", statement.lineno),
                    _hardware_options_fallback_block(source, sorted(assigned_names)),
                )
            )
        loaded_later_in_cell.update(
            node.id for node in ast.walk(statement) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        )

    if not replacements:
        return cell
    lines = cell.splitlines()
    for start, end, replacement in sorted(replacements, reverse=True):
        lines[start - 1 : end] = replacement.splitlines()
    return "\n".join(lines)


def _assigned_names(statement: ast.stmt) -> list[str]:
    if isinstance(statement, ast.Assign):
        return _target_names(statement.targets)
    if isinstance(statement, ast.AnnAssign):
        return _target_names([statement.target])
    return []


def _contains_hardware_introspection(statement: ast.AST) -> bool:
    for node in ast.walk(statement):
        if isinstance(node, ast.Attribute) and node.attr in {
            "hardware_configuration",
            "_clusters",
        }:
            return True
        if isinstance(node, ast.Call) and _call_name(node) == "get_clusters":
            return True
    return False


def _contains_hardware_configuration_access(statement: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Attribute) and node.attr == "hardware_configuration"
        for node in ast.walk(statement)
    )


def _hardware_options_fallback_block(source: str, assigned_names: list[str]) -> str:
    if len(assigned_names) != 1:
        return _comment_block("qbstimeline probe skipped hardware configuration access", source)
    target_name = assigned_names[0]
    return (
        "try:\n"
        + _indent_block(source)
        + "\n"
        "except TypeError:\n"
        f"    {target_name} = __import__('types').SimpleNamespace(\n"
        "        output_att=__import__('collections').defaultdict(lambda: None)\n"
        "    )"
    )


def _indent_block(source: str) -> str:
    return "\n".join("    " + line for line in source.splitlines())


def _comment_block(reason: str, source: str) -> str:
    lines = [f"# {reason}:"]
    for line in source.splitlines() or [""]:
        lines.append("# " + line)
    return "\n".join(lines)


def _contains_execution_call(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        name = _call_name(child)
        if name in {"display", "show"}:
            return True
        if name in {"run", "compile", "plot", "plot_pulse_diagram", "plot_circuit_diagram"}:
            return True
        if name and name.startswith("plot_"):
            return True
    return False


def _call_name(call: ast.Call) -> str | None:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def _analyze_code(code: str, *, code_cell_count: int) -> dict[str, Any]:
    schedule_candidates: list[str] = []
    compiler_candidates: list[str] = []
    parse_errors: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        tree = ast.Module(body=[], type_ignores=[])
        parse_errors.append(str(exc))

    for statement in ast.walk(tree):
        assigned_call = _assigned_call(statement)
        if assigned_call is None:
            continue
        target_names, call_name = assigned_call
        if call_name == "Schedule" or _looks_like_schedule_factory(call_name):
            schedule_candidates.extend(target_names)
        if call_name == "HardwareAgent":
            compiler_candidates.extend(target_names)

    return {
        "code_cell_count": code_cell_count,
        "code_line_count": len(code.splitlines()),
        "schedule_candidates": _unique_preserve_order(schedule_candidates),
        "compiler_candidates": _unique_preserve_order(compiler_candidates),
        "has_compile_call": bool(re.search(r"\.compile\s*\(", code)),
        "has_run_call": bool(re.search(r"\.run\s*\(", code)),
        "has_connect_clusters_call": bool(re.search(r"\bconnect_clusters\s*\(", code)),
        "parse_errors": parse_errors,
    }


def _assigned_call(node: ast.AST) -> tuple[list[str], str] | None:
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
        return (_target_names(node.targets), _call_name(node.value) or "")
    if isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Call):
        return (_target_names([node.target]), _call_name(node.value) or "")
    return None


def _looks_like_schedule_factory(call_name: str) -> bool:
    normalized = call_name.lower()
    return normalized.endswith("_schedule") or normalized.endswith("schedule")


def _target_names(targets: Iterable[ast.AST]) -> list[str]:
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            names.extend(_target_names(target.elts))
    return names


def _unique_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _warnings_for_analysis(analysis: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if not analysis["schedule_candidates"]:
        warnings.append(
            "No Schedule(...) assignment or schedule factory detected; edit schedule.py before running this probe."
        )
    if not analysis["compiler_candidates"]:
        warnings.append("No HardwareAgent(...) assignment detected; edit schedule.py before running this probe.")
    if analysis["has_run_call"]:
        warnings.append("Notebook contains run(...) calls; generated notebook_cells.py strips execution statements where possible.")
    if analysis["has_connect_clusters_call"]:
        warnings.append("Notebook contains connect_clusters(); review hardware safety before running this probe.")
    return warnings


def _render_schedule_wrapper(
    *,
    notebook_dir: Path,
    schedule_candidates: Sequence[str],
    compiler_candidates: Sequence[str],
) -> str:
    schedule_tuple = _tuple_literal(schedule_candidates)
    compiler_tuple = _tuple_literal(compiler_candidates)
    return f'''from __future__ import annotations

import os
import copy
import runpy
import sys
from pathlib import Path


NOTEBOOK_DIR = Path(r"{notebook_dir}")
NOTEBOOK_CELLS = Path(__file__).with_name("notebook_cells.py")
SCHEDULE_CANDIDATES = {schedule_tuple}
COMPILER_CANDIDATES = {compiler_tuple}
COMPACT_CONTROL_FLOW_EXPANSION_LIMIT = 1000
MANUAL_SWEEP_COMPACT_MIN = 50
_NAMESPACE = None


class _HardwareAgentCompiler:
    def __init__(self, target):
        self._target = target

    def compile(self, schedule):
        compile_method = getattr(self._target, "compile", None)
        if not callable(compile_method):
            raise RuntimeError(f"{{type(self._target).__name__}} does not provide compile(schedule)")
        if _control_flow_expansion(schedule) > COMPACT_CONTROL_FLOW_EXPANSION_LIMIT:
            representative = _representative_schedule(schedule)
            if representative is schedule or _control_flow_expansion(representative) > COMPACT_CONTROL_FLOW_EXPANSION_LIMIT:
                return schedule
            return compile_method(representative)
        return compile_method(schedule)


def _load_namespace():
    global _NAMESPACE
    if _NAMESPACE is not None:
        return _NAMESPACE

    old_cwd = os.getcwd()
    sys.path.insert(0, str(NOTEBOOK_DIR))
    sys.path.insert(0, str(NOTEBOOK_DIR / "dependencies"))
    try:
        os.chdir(NOTEBOOK_DIR)
        _NAMESPACE = runpy.run_path(str(NOTEBOOK_CELLS))
    finally:
        os.chdir(old_cwd)
    return _NAMESPACE


def _unwrap(value):
    try:
        return getattr(value, "data", value)
    except Exception:
        return value


def _safe_getattr(value, name, default=None):
    try:
        return getattr(value, name, default)
    except Exception:
        return default


def _control_flow_info(operation):
    data = _unwrap(operation)
    if isinstance(data, dict):
        info = data.get("control_flow_info", {{}})
        return info if isinstance(info, dict) else {{}}
    return {{}}


def _control_flow_body(operation, info):
    body = info.get("body") if isinstance(info, dict) else None
    if body is not None:
        return body
    if not isinstance(info, dict) or not any(key in info for key in ("repetitions", "domain", "body", "t0")):
        return None
    return _safe_getattr(operation, "body", None)


def _nested_schedule_body(operation):
    body = _safe_getattr(operation, "body", None)
    if _is_schedule_like(body):
        return body
    if _is_schedule_like(operation):
        return operation
    return None


def _is_schedule_like(value):
    if value is None:
        return False
    schedulables = _safe_getattr(value, "schedulables", None)
    operations = _safe_getattr(value, "operations", None) or _safe_getattr(value, "operation_dict", None)
    return isinstance(schedulables, dict) and isinstance(operations, dict)


def _domain_size(info):
    domain = info.get("domain") if isinstance(info, dict) else None
    if isinstance(domain, dict):
        sizes = []
        for value in domain.values():
            size = getattr(value, "num", None)
            if isinstance(size, int | float) and size > 0:
                sizes.append(int(size))
        if sizes:
            result = 1
            for size in sizes:
                result *= size
            return result
    repetitions = info.get("repetitions") if isinstance(info, dict) else None
    return int(repetitions) if isinstance(repetitions, int | float) and repetitions > 0 else 1


def _schedule_repetitions(schedule):
    repetitions = _safe_getattr(schedule, "repetitions", 1)
    return int(repetitions) if isinstance(repetitions, int | float) and repetitions > 0 else 1


def _representative_schedule(schedule):
    if _schedule_repetitions(schedule) <= 1:
        return schedule
    try:
        representative = copy.deepcopy(schedule)
    except Exception:
        return schedule
    try:
        representative.repetitions = 1
    except Exception:
        return schedule
    return representative


def _manual_sweep_expansion(schedulables, operations):
    if len(schedulables) < MANUAL_SWEEP_COMPACT_MIN:
        return 1
    labels = set()
    max_nested = 1
    for schedulable in schedulables.values():
        schedulable_data = _unwrap(schedulable)
        if not isinstance(schedulable_data, dict):
            return 1
        operation = operations.get(schedulable_data.get("operation_id"))
        if _control_flow_info(operation):
            return 1
        nested_body = _nested_schedule_body(operation)
        if nested_body is None:
            return 1
        data = _unwrap(operation)
        label = _safe_getattr(operation, "name", None)
        if not label and isinstance(data, dict):
            label = data.get("name")
        labels.add(str(label or type(operation).__name__))
        if len(labels) > 1:
            return 1
        max_nested = max(max_nested, _control_flow_expansion(nested_body))
    return len(schedulables) * max_nested


def _experiment_schedules(schedule):
    experiments = _safe_getattr(schedule, "_experiments", None)
    if not isinstance(experiments, list):
        return []
    schedules = []
    for experiment in experiments:
        if not isinstance(experiment, dict):
            continue
        steps = experiment.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            schedule_info = step.get("schedule_info")
            if not isinstance(schedule_info, dict):
                continue
            nested_schedule = schedule_info.get("schedule")
            if nested_schedule is not None:
                schedules.append(nested_schedule)
    return schedules


def _control_flow_expansion(schedule):
    schedulables = _safe_getattr(schedule, "schedulables", None)
    operations = _safe_getattr(schedule, "operations", None) or _safe_getattr(schedule, "operation_dict", None)
    if not isinstance(schedulables, dict) or not isinstance(operations, dict):
        max_expansion = 1
        for nested_schedule in _experiment_schedules(schedule):
            max_expansion = max(max_expansion, _control_flow_expansion(nested_schedule))
        return max_expansion
    max_expansion = _schedule_repetitions(schedule) * _manual_sweep_expansion(schedulables, operations)
    for schedulable in schedulables.values():
        schedulable_data = _unwrap(schedulable)
        if not isinstance(schedulable_data, dict):
            continue
        operation = operations.get(schedulable_data.get("operation_id"))
        info = _control_flow_info(operation)
        body = _control_flow_body(operation, info)
        if body is not None:
            max_expansion = max(max_expansion, _domain_size(info) * _control_flow_expansion(body))
            continue
        nested_body = _nested_schedule_body(operation)
        if nested_body is not None:
            max_expansion = max(max_expansion, _control_flow_expansion(nested_body))
    return max_expansion


def build_schedule():
    if not SCHEDULE_CANDIDATES:
        raise RuntimeError(
            "No build_schedule() function or detected Schedule(...) variable was available. "
            "Edit this generated schedule.py and return the notebook schedule explicitly."
        )
    namespace = _load_namespace()
    entrypoint = namespace.get("build_schedule")
    if callable(entrypoint):
        return entrypoint()
    for name in SCHEDULE_CANDIDATES:
        if name in namespace:
            return namespace[name]
    raise RuntimeError(
        "No build_schedule() function or detected Schedule(...) variable was available. "
        "Edit this generated schedule.py and return the notebook schedule explicitly."
    )


def build_compiler():
    if not COMPILER_CANDIDATES:
        raise RuntimeError(
            "No build_compiler() function or detected HardwareAgent(...) variable was available. "
            "Edit this generated schedule.py and return an object with compile(schedule)."
        )
    namespace = _load_namespace()
    entrypoint = namespace.get("build_compiler")
    if callable(entrypoint):
        return entrypoint()
    for name in COMPILER_CANDIDATES:
        if name in namespace:
            return _HardwareAgentCompiler(namespace[name])
    raise RuntimeError(
        "No build_compiler() function or detected HardwareAgent(...) variable was available. "
        "Edit this generated schedule.py and return an object with compile(schedule)."
    )
'''


def _tuple_literal(values: Sequence[str]) -> str:
    if not values:
        return "()"
    if len(values) == 1:
        return f"({values[0]!r},)".replace("'", '"')
    return repr(tuple(values)).replace("'", '"')


def _render_project_config(*, notebook_path: Path) -> str:
    return f"""schedule:
  file: schedule.py
  entrypoint: build_schedule
  compiler: build_compiler

source:
  notebook: {notebook_path}

outputs:
  dir: .qbs_timeline

low_level:
  q1timeline: true
"""


def _safe_probe_name(page_slug: str, notebook_stem: str) -> str:
    raw = f"{page_slug}__{notebook_stem}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("_")


def _clean_output_dir(output_dir: Path) -> None:
    output_dir = output_dir.resolve()
    if not output_dir.exists():
        return
    if output_dir.anchor == str(output_dir):
        raise ValueError(f"Refusing to remove filesystem root: {output_dir}")
    shutil.rmtree(output_dir)


def _run_subprocess(command: Sequence[str], *, cwd: Path, timeout: int) -> CompletedProbeRun:
    start = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            timeout=timeout,
            text=True,
            capture_output=True,
            check=False,
        )
        return CompletedProbeRun(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=time.monotonic() - start,
        )
    except subprocess.TimeoutExpired as exc:
        return CompletedProbeRun(
            exit_code=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or f"Timed out after {timeout} seconds",
            duration_seconds=time.monotonic() - start,
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare qbstimeline probes for downloaded Qblox application notebooks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Generate .py/qbstimeline.yml probe projects for application notebooks.")
    prepare.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to qblox_application_examples/manifest.json.")
    prepare.add_argument("--out", default=str(DEFAULT_OUTPUT_DIR), help="Output directory for generated probes.")
    prepare.add_argument("--clean", action="store_true", help="Remove the output directory before generating probes.")

    run = subparsers.add_parser("run", help="Run qbstimeline analyze for every generated probe.")
    run.add_argument("--manifest", default=str(DEFAULT_OUTPUT_DIR / "probe_manifest.json"), help="Generated probe_manifest.json.")
    run.add_argument("--timeout", type=int, default=90, help="Per-probe timeout in seconds.")

    args = parser.parse_args(argv)
    if args.command == "prepare":
        manifest = prepare_probes(args.manifest, args.out, clean=args.clean)
        print(f"Wrote {manifest['probe_count']} probe project(s) to {manifest['output_dir']}")
        print(f"Detected Schedule(...) candidates in {manifest['ready_count']} probe(s)")
        return 0
    if args.command == "run":
        report = run_probe_projects(args.manifest, timeout_seconds=args.timeout)
        print(
            f"Ran {report['run_count']} probe project(s); "
            f"{report['ok_count']} succeeded, {report['skip_count']} skipped, {report['fail_count']} failed"
        )
        print(f"Wrote report: {Path(args.manifest).resolve().parent / 'probe_run_report.json'}")
        return 0 if report["fail_count"] == 0 else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
