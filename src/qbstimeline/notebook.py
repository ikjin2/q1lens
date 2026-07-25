from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class NotebookCodeCell:
    cell_index: int
    cell_id: str | None
    source: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class SelectedNotebookCells:
    setup_cells: tuple[NotebookCodeCell, ...]
    schedule_cell: NotebookCodeCell


@dataclass(frozen=True)
class NotebookSourceLocation:
    file: Path | None
    cell_index: int
    cell_id: str | None
    cell_line: int


class GeneratedLineMapper:
    _MARKER_RE = re.compile(r"^# %% qbstimeline notebook cell (?P<cell>[1-9][0-9]*)\s*$")

    def __init__(self, markers: tuple[tuple[int, int], ...], file: Path | None = None) -> None:
        self._markers = markers
        self._file = file

    @classmethod
    def from_source(cls, source: str, *, file: Path | None = None) -> GeneratedLineMapper:
        markers: list[tuple[int, int]] = []
        for line_number, line in enumerate(source.splitlines(), start=1):
            match = cls._MARKER_RE.match(line)
            if match is None:
                continue
            markers.append((line_number, int(match.group("cell")) - 1))
        return cls(tuple(markers), file=file)

    def location_for_generated_line(self, generated_line: int) -> NotebookSourceLocation | None:
        marker: tuple[int, int] | None = None
        for candidate in self._markers:
            if candidate[0] >= generated_line:
                break
            marker = candidate
        if marker is None:
            return None
        marker_line, cell_index = marker
        return NotebookSourceLocation(
            file=self._file,
            cell_index=cell_index,
            cell_id=None,
            cell_line=generated_line - marker_line,
        )


def load_notebook_code_cells(path: Path) -> tuple[NotebookCodeCell, ...]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    cells = []
    for index, cell in enumerate(raw.get("cells", [])):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        if isinstance(source, list):
            source_text = "".join(str(line) for line in source)
        else:
            source_text = str(source)
        metadata = cell.get("metadata", {})
        tags = metadata.get("tags", []) if isinstance(metadata, dict) else []
        cells.append(
            NotebookCodeCell(
                cell_index=index,
                cell_id=cell.get("id") if isinstance(cell.get("id"), str) else None,
                source=source_text,
                tags=tuple(str(tag) for tag in tags if isinstance(tag, str)),
            )
        )
    return tuple(cells)


def select_tagged_notebook_cells(
    cells: tuple[NotebookCodeCell, ...],
    *,
    setup_tags: tuple[str, ...],
    schedule_tag: str,
) -> SelectedNotebookCells:
    setup_tag_set = set(setup_tags)
    setup_cells = tuple(cell for cell in cells if setup_tag_set.intersection(cell.tags))
    schedule_cells = tuple(cell for cell in cells if schedule_tag in cell.tags)
    if len(schedule_cells) != 1:
        raise ValueError(
            f"Expected exactly one notebook schedule cell tagged '{schedule_tag}', found {len(schedule_cells)}"
        )
    return SelectedNotebookCells(setup_cells=setup_cells, schedule_cell=schedule_cells[0])


def execute_selected_notebook_cells(
    notebook_path: Path,
    *,
    setup_tags: tuple[str, ...],
    schedule_tag: str,
) -> dict[str, Any]:
    cells = load_notebook_code_cells(notebook_path)
    selected = select_tagged_notebook_cells(
        cells,
        setup_tags=setup_tags,
        schedule_tag=schedule_tag,
    )
    namespace: dict[str, Any] = {"__name__": "__qbstimeline_notebook__"}
    source_root = str(notebook_path.parent.resolve())
    inserted = source_root not in sys.path
    if inserted:
        sys.path.insert(0, source_root)
    try:
        for cell in (*selected.setup_cells, selected.schedule_cell):
            filename = f"{notebook_path}#cell-{cell.cell_index}"
            try:
                exec(compile(cell.source, filename, "exec"), namespace)
            except Exception as exc:
                raise RuntimeError(
                    f"Notebook execution failed in {notebook_path} cell {cell.cell_index}: {exc}"
                ) from exc
    finally:
        if inserted:
            try:
                sys.path.remove(source_root)
            except ValueError:
                pass
    return namespace
