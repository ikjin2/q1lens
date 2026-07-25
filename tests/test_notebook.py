from __future__ import annotations

import json
from pathlib import Path

from qbstimeline.notebook import (
    GeneratedLineMapper,
    execute_selected_notebook_cells,
    load_notebook_code_cells,
    select_tagged_notebook_cells,
)


def _write_notebook(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "cells": [
                    {"cell_type": "markdown", "source": ["# ignored"], "metadata": {}},
                    {
                        "cell_type": "code",
                        "source": ["setup_value = 1\n"],
                        "metadata": {"tags": ["qbstimeline-setup"]},
                        "id": "setup-cell",
                    },
                    {
                        "cell_type": "code",
                        "source": ["two_tone_sched = Schedule('two_tone')\n"],
                        "metadata": {"tags": ["qbstimeline-schedule"]},
                        "id": "schedule-cell",
                    },
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )


def test_select_tagged_notebook_cells(tmp_path: Path) -> None:
    notebook = tmp_path / "experiment.ipynb"
    _write_notebook(notebook)

    cells = load_notebook_code_cells(notebook)
    selected = select_tagged_notebook_cells(
        cells,
        setup_tags=("qbstimeline-setup",),
        schedule_tag="qbstimeline-schedule",
    )

    assert [cell.cell_index for cell in selected.setup_cells] == [1]
    assert selected.schedule_cell.cell_index == 2
    assert selected.schedule_cell.cell_id == "schedule-cell"


def test_load_notebook_code_cells_accepts_utf8_bom(tmp_path: Path) -> None:
    notebook = tmp_path / "experiment.ipynb"
    _write_notebook(notebook)
    notebook.write_text("\ufeff" + notebook.read_text(encoding="utf-8"), encoding="utf-8")

    cells = load_notebook_code_cells(notebook)

    assert [cell.cell_id for cell in cells] == ["setup-cell", "schedule-cell"]


def test_generated_line_mapper_maps_lines_to_notebook_cells() -> None:
    source = "\n".join(
        [
            "# %% qbstimeline notebook cell 2",
            "setup_value = 1",
            "# %% qbstimeline notebook cell 3",
            "two_tone_sched = Schedule('two_tone')",
            "two_tone_sched.add(Measure('q0'))",
        ]
    )
    mapper = GeneratedLineMapper.from_source(source)

    location = mapper.location_for_generated_line(5)

    assert location is not None
    assert location.cell_index == 2
    assert location.cell_line == 2


def test_execute_selected_notebook_cells_runs_only_tagged_cells(tmp_path: Path) -> None:
    notebook = tmp_path / "experiment.ipynb"
    notebook.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "source": ["raise RuntimeError('untagged cell must not run')\n"],
                        "metadata": {},
                    },
                    {
                        "cell_type": "code",
                        "source": ["setup_value = 41\n"],
                        "metadata": {"tags": ["qbstimeline-setup"]},
                    },
                    {
                        "cell_type": "code",
                        "source": ["two_tone_sched = setup_value + 1\nhw_agent = object()\n"],
                        "metadata": {"tags": ["qbstimeline-schedule"]},
                    },
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )

    namespace = execute_selected_notebook_cells(
        notebook,
        setup_tags=("qbstimeline-setup",),
        schedule_tag="qbstimeline-schedule",
    )

    assert namespace["two_tone_sched"] == 42
    assert "hw_agent" in namespace


def test_execute_selected_notebook_cells_can_import_sibling_helpers(tmp_path: Path) -> None:
    notebook = tmp_path / "experiment.ipynb"
    (tmp_path / "helper.py").write_text("VALUE = 41\n", encoding="utf-8")
    notebook.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "source": ["from helper import VALUE\n"],
                        "metadata": {"tags": ["qbstimeline-setup"]},
                    },
                    {
                        "cell_type": "code",
                        "source": ["two_tone_sched = VALUE + 1\n"],
                        "metadata": {"tags": ["qbstimeline-schedule"]},
                    },
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )

    namespace = execute_selected_notebook_cells(
        notebook,
        setup_tags=("qbstimeline-setup",),
        schedule_tag="qbstimeline-schedule",
    )

    assert namespace["two_tone_sched"] == 42
