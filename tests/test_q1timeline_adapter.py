from __future__ import annotations

from pathlib import Path

import yaml

from qbstimeline.adapters.q1timeline import write_q1timeline_project
from qbstimeline.compile_worker import Q1ASMProgram


def test_write_q1timeline_project_uses_cli_level_project_contract(tmp_path: Path) -> None:
    project_file = write_q1timeline_project(
        tmp_path,
        [
            Q1ASMProgram(
                sequencer_id="cluster0_module0_seq0",
                relative_file=Path("q1asm/cluster0_module0_seq0.q1asm"),
                program="wait_sync 4\nstop\n",
                path=("cluster0", "module0", "seq0"),
            )
        ],
    )

    payload = yaml.safe_load(project_file.read_text(encoding="utf-8"))

    assert project_file == tmp_path / "q1timeline.yml"
    assert payload["sequencers"] == [
        {
            "id": "cluster0_module0_seq0",
            "name": "cluster0/module0/seq0",
            "file": "q1asm/cluster0_module0_seq0.q1asm",
            "module": "cluster0/module0",
        }
    ]
    assert payload["alignment"]["mode"] == "after_first_wait_sync"
