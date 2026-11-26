from __future__ import annotations

from pathlib import Path

import yaml

from qbstimeline.compile_worker import Q1ASMProgram


def write_q1timeline_project(output_dir: Path, programs: list[Q1ASMProgram], *,
                            module_types: dict[str, str] | None = None) -> Path:
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
                **({"sequence_json": program.relative_file.with_suffix(".sequence.json").as_posix()}
                   if program.sequence is not None else {}),
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
    modules = {}
    for program in programs:
        name = "/".join(program.path[:-1])
        if (module_types and name in module_types and program.module_type is not None
                and program.module_type != module_types[name]):
            raise ValueError(f"Conflicting module types for {name}")
        kind = (module_types or {}).get(name, program.module_type)
        kind = str(kind).strip().upper().replace("_", "-") if kind is not None else None
        if kind not in ("QCM", "QRM", "QCM-RF", "QRM-RF"):
            continue
        rf = kind.endswith("-RF")
        settings = program.settings
        channel = settings.get("channel_name", "")
        if "connected_output_indices" in settings and channel.startswith(("real_", "complex_")):
            if rf:
                # An RF output takes both DAC paths of one sequencer: indices 0,1 feed out0 and 2,3 feed out1.
                routes = {f"out{i // 2}": "IQ" for i in settings["connected_output_indices"]}
            else:
                routes = {f"out{i}": "Q" if channel.startswith("complex_") and i % 2 else "I"
                          for i in settings["connected_output_indices"]}
        else:
            routes = {key[8:]: path for key, path in settings.items()
                      if key.startswith("connect_out") and path != "off"}
        module = modules.setdefault(name, {
            "type": kind, **({"cluster": program.path[0]} if len(program.path) >= 3 else {}),
            "channel_map": {}, **({"lo_frequency_hz": {}} if rf else {"output_offsets": {}}),
        })
        module["channel_map"][program.sequencer_id] = routes
        if rf:
            for i in range(2 if kind == "QCM-RF" else 1):
                hertz = program.module_settings.get(f"lo{i}_freq")
                if isinstance(hertz, (int, float)) and not isinstance(hertz, bool) and hertz >= 0:
                    module["lo_frequency_hz"][f"out{i}"] = hertz
            continue
        for i in range(4 if kind == "QCM" else 2):
            native = program.module_settings.get(f"offset_ch{i // 2}_path_{'IQ'[i % 2]}")
            volts = program.module_settings.get(f"out{i}_offset", native)
            if volts is not None:
                module["output_offsets"][f"out{i}"] = volts / (2.5 if kind == "QCM" else 0.5)
    if modules:
        payload["modules"] = modules
    project_file.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return project_file
