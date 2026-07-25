from __future__ import annotations

from pathlib import Path

import yaml

from qbstimeline.compile_worker import Q1ASMProgram


def write_q1timeline_project(output_dir: Path, programs: list[Q1ASMProgram]) -> Path:
    """Write a q1timeline.yml project without importing q1timeline internals."""
    project_file = output_dir / "q1timeline.yml"
    project_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sequencers": [
            {
                "id": program.sequencer_id,
                "name": "/".join(program.path),
                "file": program.relative_file.as_posix(),
                "module": "/".join(program.path[:-1]),
            }
            for program in programs
        ],
        "alignment": {
            "mode": "after_first_wait_sync",
        },
        "view": {
            "default_mode": "normal",
            "show_q1_issue": False,
            "show_queue": False,
            "show_slack": False,
            "show_loop_preview": True,
        },
        "analysis": {
            "loop_policy": "compact_first_iteration",
            "branch_policy": "collapse_unresolved",
            "underflow_policy": "confidence_levels",
        },
    }
    project_file.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return project_file
