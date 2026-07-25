from __future__ import annotations

import json
from pathlib import Path
from typing import Any


QBS_IR_SCHEMA_VERSION = "0.1.0"


def make_qbs_ir(
    *,
    project_root: Path,
    schedule_name: str,
    operations: list[dict[str, Any]],
    timing_table: list[dict[str, Any]],
    q1asm_programs: list[Any],
    low_level_q1timeline: bool,
    control_flow_blocks: list[dict[str, Any]] | None = None,
    symbolic_values: list[dict[str, Any]] | None = None,
    symbolic_pulses: list[dict[str, Any]] | None = None,
    q1asm_provenance: list[dict[str, Any]] | None = None,
    source_map: dict[str, Any] | None = None,
    capabilities: dict[str, bool] | None = None,
    warnings: list[str] | None = None,
    ir_diagnostics: list[dict[str, Any]] | None = None,
    artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    symbolic_values = symbolic_values or []
    symbolic_pulses = symbolic_pulses or []
    q1asm_provenance = q1asm_provenance or []
    artifacts = artifacts or {}
    return {
        "version": QBS_IR_SCHEMA_VERSION,
        "status": "ok",
        "project": {
            "root": str(project_root),
            "low_level_q1timeline": low_level_q1timeline,
        },
        "schedule": {
            "name": schedule_name,
        },
        "operations": operations,
        "control_flow_blocks": control_flow_blocks or [],
        "timing_table": timing_table,
        "symbolic_values": symbolic_values,
        "symbolic_pulses": symbolic_pulses,
        "q1asm_provenance": q1asm_provenance,
        "source_map": source_map or {},
        "capabilities": capabilities
        or {
            "operations": bool(operations),
            "symbolic_pulses": bool(symbolic_pulses),
            "q1asm": bool(q1asm_programs),
            "artifacts": bool(artifacts),
        },
        "warnings": warnings or [],
        "ir_diagnostics": ir_diagnostics or [],
        "artifacts": artifacts,
        "q1asm_programs": [
            {
                "sequencer_id": program.sequencer_id,
                "file": program.relative_file.as_posix(),
                "path": list(program.path),
            }
            for program in q1asm_programs
        ],
        "q1asm_by_sequencer": {
            program.sequencer_id: program.program for program in q1asm_programs
        },
    }


def qbs_ir_to_json(ir: dict[str, Any]) -> str:
    return json.dumps(ir, indent=2, sort_keys=True, allow_nan=False) + "\n"


def write_qbs_ir(ir: dict[str, Any], path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(qbs_ir_to_json(ir), encoding="utf-8")
