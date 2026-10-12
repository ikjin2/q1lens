from __future__ import annotations

import ast
import json
import os
import platform
import sys
import tokenize
from importlib import metadata
from pathlib import Path
from typing import Any

from qbstimeline import __version__
from qbstimeline.notebook import NotebookCodeCell, load_notebook_code_cells
from qbstimeline.project import ConfigLoadError, NotebookScheduleConfig, ProjectConfig, load_project_config


DOCTOR_REPORT_VERSION = "1.0"


def inspect_project(path: str | Path) -> dict[str, Any]:
    """Inspect a project without importing or executing user code."""
    project_file = Path(path).resolve()
    checks: list[dict[str, Any]] = []
    try:
        config = load_project_config(project_file)
    except ConfigLoadError as exc:
        checks.append(_check("project.config", False, str(exc), path=project_file))
        return _report(project_file, checks)
    except UnicodeError as exc:
        checks.append(
            _check(
                "project.config",
                False,
                f"Project configuration must use UTF-8 encoding: {exc}",
                path=project_file,
            )
        )
        return _report(project_file, checks)

    mode = "notebook" if config.notebook_schedule is not None else "python"
    checks.append(
        _check(
            "project.config",
            True,
            "Project configuration parsed",
            mode=mode,
            output_dir=config.output_dir,
        )
    )

    if config.notebook_schedule is not None:
        checks.extend(_inspect_notebook_schedule(config.notebook_schedule))
    else:
        checks.extend(_inspect_python_schedule(config))

    checks.append(_inspect_output_directory(config.output_dir))
    return _report(project_file, checks)


def format_report(report: dict[str, Any], *, prog: str) -> str:
    status = str(report["status"]).upper()
    environment = report["environment"]
    python = environment["python"]
    package = environment["package"]
    lines = [
        f"{prog} doctor: {status}",
        f"Project: {report['project']}",
        f"Python: {python['version']} ({python['executable']})",
        f"Package: {package['name']} {package['version']} ({package['module_path']})",
    ]
    for check in report["checks"]:
        lines.append(f"[{str(check['status']).upper()}] {check['id']}: {check['message']}")
    summary = report["summary"]
    lines.append(
        f"Checks: {summary['passed']} passed, {summary['failed']} failed, "
        f"{summary['total']} total"
    )
    return "\n".join(lines) + "\n"


def _inspect_python_schedule(config: ProjectConfig) -> list[dict[str, Any]]:
    assert config.schedule_file is not None
    schedule_file = config.schedule_file
    source_ok = schedule_file.is_file()
    checks = [
        _check(
            "schedule.source",
            source_ok,
            "Schedule source exists"
            if source_ok
            else "Schedule source does not exist or is not a file",
            kind="python",
            path=schedule_file,
        )
    ]
    checks.append(
        _inspect_python_entrypoints(
            schedule_file,
            (config.schedule_entrypoint, config.compiler_entrypoint),
        )
    )
    if config.source_notebook is not None:
        checks.append(_inspect_source_notebook(config.source_notebook))
    return checks


def _inspect_python_entrypoints(path: Path, entrypoints: tuple[str, str]) -> dict[str, Any]:
    if not path.is_file():
        return _check(
            "schedule.entrypoints",
            False,
            "Could not inspect entrypoints because the schedule source is unavailable",
            expected=list(entrypoints),
            missing=list(entrypoints),
            path=path,
        )
    try:
        with tokenize.open(path) as source_file:
            tree = ast.parse(source_file.read(), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        return _check(
            "schedule.entrypoints",
            False,
            f"Could not parse schedule source: {_exception_message(exc)}",
            expected=list(entrypoints),
            missing=list(entrypoints),
            path=path,
        )

    bindings = {name: kind for name, kind in _module_binding_changes(tree).items() if kind is not None}
    missing = [name for name in entrypoints if name not in bindings]
    found = {name: bindings[name] for name in entrypoints if name in bindings}
    return _check(
        "schedule.entrypoints",
        not missing,
        "Configured schedule entrypoints are present"
        if not missing
        else f"Missing configured entrypoint(s): {', '.join(missing)}",
        expected=list(entrypoints),
        found=found,
        missing=missing,
        path=path,
    )


def _inspect_source_notebook(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return _check(
            "source.notebook",
            False,
            "Referenced source notebook does not exist or is not a file",
            path=path,
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        _validate_notebook_shape(raw)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return _check(
            "source.notebook",
            False,
            f"Referenced source notebook is invalid: {_exception_message(exc)}",
            path=path,
        )
    return _check(
        "source.notebook",
        True,
        "Referenced source notebook is valid JSON",
        path=path,
    )


def _inspect_notebook_schedule(config: NotebookScheduleConfig) -> list[dict[str, Any]]:
    path = config.notebook
    source_ok = path.is_file()
    checks = [
        _check(
            "schedule.source",
            source_ok,
            "Schedule notebook exists"
            if source_ok
            else "Schedule notebook does not exist or is not a file",
            kind="notebook",
            path=path,
        )
    ]
    if not source_ok:
        checks.append(
            _check(
                "notebook.json",
                False,
                "Could not inspect notebook JSON because the notebook is unavailable",
                path=path,
            )
        )
        return checks

    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        _validate_notebook_shape(raw)
        cells = load_notebook_code_cells(path)
    except (OSError, json.JSONDecodeError, ValueError, AttributeError, TypeError) as exc:
        checks.append(
            _check(
                "notebook.json",
                False,
                f"Notebook JSON is invalid: {_exception_message(exc)}",
                path=path,
            )
        )
        return checks

    checks.append(
        _check(
            "notebook.json",
            True,
            "Notebook JSON is valid",
            code_cell_count=len(cells),
            path=path,
        )
    )
    schedule_cells = tuple(cell for cell in cells if config.schedule_tag in cell.tags)
    setup_tag_counts = {
        tag: sum(tag in cell.tags for cell in cells)
        for tag in config.setup_tags
    }
    missing_setup_tags = [tag for tag, count in setup_tag_counts.items() if count == 0]
    tags_ok = len(schedule_cells) == 1 and not missing_setup_tags
    if len(schedule_cells) != 1:
        tag_message = (
            f"Expected exactly one schedule cell tagged '{config.schedule_tag}', "
            f"found {len(schedule_cells)}"
        )
    elif missing_setup_tags:
        tag_message = f"Configured setup tag(s) not found: {', '.join(missing_setup_tags)}"
    else:
        tag_message = "Configured notebook tags are present"
    checks.append(
        _check(
            "notebook.tags",
            tags_ok,
            tag_message,
            schedule_cell_indices=[cell.cell_index for cell in schedule_cells],
            schedule_tag=config.schedule_tag,
            setup_tag_counts=setup_tag_counts,
        )
    )
    if not tags_ok:
        return checks

    setup_cells = tuple(
        cell for cell in cells if any(tag in cell.tags for tag in config.setup_tags)
    )
    selected_cells = (*setup_cells, schedule_cells[0])
    checks.append(
        _inspect_notebook_variables(
            selected_cells,
            (config.schedule_variable, config.compiler_variable),
        )
    )
    return checks


def _inspect_notebook_variables(
    cells: tuple[NotebookCodeCell, ...],
    variables: tuple[str, str],
) -> dict[str, Any]:
    bindings: dict[str, dict[str, Any]] = {}
    for cell in cells:
        try:
            tree = ast.parse(cell.source, filename=f"notebook-cell-{cell.cell_index}")
        except (SyntaxError, ValueError) as exc:
            return _check(
                "notebook.variables",
                False,
                f"Could not parse selected notebook cell {cell.cell_index}: {_exception_message(exc)}",
                expected=list(variables),
                missing=list(variables),
            )
        for name, kind in _module_binding_changes(tree).items():
            if kind is None:
                bindings.pop(name, None)
            else:
                bindings[name] = {"cell_index": cell.cell_index, "kind": kind}

    missing = [name for name in variables if name not in bindings]
    found = {name: bindings[name] for name in variables if name in bindings}
    return _check(
        "notebook.variables",
        not missing,
        "Configured notebook variables are present in selected cells"
        if not missing
        else f"Missing configured notebook variable(s): {', '.join(missing)}",
        expected=list(variables),
        found=found,
        missing=missing,
    )


def _inspect_output_directory(output_dir: Path) -> dict[str, Any]:
    candidate = output_dir
    try:
        while not candidate.exists() and candidate.parent != candidate:
            candidate = candidate.parent
        if not candidate.exists() or not candidate.is_dir():
            return _check(
                "output.writable_ancestor",
                False,
                "Output path is blocked before an existing directory is reached",
                output_dir=output_dir,
                existing_path=candidate,
            )
        writable = os.access(candidate, os.W_OK)
    except OSError as exc:
        return _check(
            "output.writable_ancestor",
            False,
            f"Could not inspect output path: {_exception_message(exc)}",
            output_dir=output_dir,
        )
    return _check(
        "output.writable_ancestor",
        writable,
        "Output directory has an existing writable ancestor"
        if writable
        else "Nearest existing output ancestor is not writable",
        ancestor=candidate,
        output_dir=output_dir,
    )


def _module_binding_changes(tree: ast.Module) -> dict[str, str | None]:
    """Return each name's final change, retaining deletions for later notebook cells."""
    bindings: dict[str, str | None] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bindings[node.name] = "function"
        elif isinstance(node, ast.ClassDef):
            bindings[node.name] = "class"
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for name in _target_names(target):
                    bindings[name] = "assignment"
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            for name in _target_names(node.target):
                bindings[name] = "assignment"
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                for name in _target_names(target):
                    bindings[name] = None
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bindings[alias.asname or alias.name.split(".", 1)[0]] = "import"
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    bindings[alias.asname or alias.name] = "import"
    return bindings


def _target_names(target: ast.expr) -> tuple[str, ...]:
    if isinstance(target, ast.Name):
        return (target.id,)
    if isinstance(target, (ast.Tuple, ast.List)):
        return tuple(name for item in target.elts for name in _target_names(item))
    return ()


def _validate_notebook_shape(raw: Any) -> None:
    if not isinstance(raw, dict):
        raise ValueError("Notebook root must be a JSON object")
    if not isinstance(raw.get("cells"), list):
        raise ValueError("Notebook must contain a cells array")


def _report(project_file: Path, checks: list[dict[str, Any]]) -> dict[str, Any]:
    failed = sum(check["status"] == "fail" for check in checks)
    passed = len(checks) - failed
    return {
        "version": DOCTOR_REPORT_VERSION,
        "status": "pass" if failed == 0 else "fail",
        "project": str(project_file),
        "environment": _environment(),
        "summary": {"failed": failed, "passed": passed, "total": len(checks)},
        "checks": checks,
    }


def _environment() -> dict[str, Any]:
    try:
        distribution = metadata.distribution("q1lens")
    except metadata.PackageNotFoundError:
        installed = False
        installed_path = None
        version = __version__
    else:
        installed = True
        installed_path = str(Path(distribution.locate_file("")).resolve())
        version = distribution.version
    return {
        "python": {
            "executable": sys.executable,
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "virtual_environment": sys.prefix != sys.base_prefix,
        },
        "package": {
            "installed": installed,
            "installed_path": installed_path,
            "module_path": str(Path(__file__).resolve().parent),
            "name": "q1lens",
            "version": version,
        },
    }


def _check(check_id: str, passed: bool, message: str, **details: Any) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": "pass" if passed else "fail",
        "required": True,
        "message": message,
        "details": {key: _json_value(value) for key, value in details.items()},
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _exception_message(exc: BaseException) -> str:
    if isinstance(exc, SyntaxError):
        location = f"line {exc.lineno}" if exc.lineno is not None else "unknown line"
        return f"{exc.msg} ({location})"
    return str(exc)
